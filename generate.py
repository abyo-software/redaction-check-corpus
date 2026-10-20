#!/usr/bin/env python3
"""Build the redaction-checker test corpus.

One file per failure mode, written as raw PDF bytes with nothing but the
standard library, so the same bytes come out on Windows and Linux. A
library would get in the way here: these documents have to be WRONG in
one specific way each ("a black rectangle laid over live text", "an OCR
layer left under the blackout"), and a well-behaved writer will not
produce that on request.

Output is deterministic — fixed dates, fixed object order, no randomness
— so the corpus can be committed and diffed.

Expected verdicts live in manifest.json, never here: one source of truth.

Numbering follows the checker requirements:
  #1-#15  the shared corpus
  #16+    editor-only cases (generated on that side)
  #90+    locally generated cases: stand-ins for ones whose real article
          has to come from a specific application (#12 Acrobat, #13 the
          abyo editor). They are NOT substitutes in the manifest; they
          exist so this side can test for false positives today.

Usage: python3 generate.py [outdir]
"""

import hashlib
import os
import re
import struct
import sys
import zlib

SECRET = "Yamada Taro 1980-04-01 Yokohama"
PAGE_W, PAGE_H = 595, 842


class Pdf:
    """Minimal PDF writer: indirect objects, xref table, trailer."""

    def __init__(self):
        self.objects: list[bytes | None] = []

    def reserve(self) -> int:
        self.objects.append(None)
        return len(self.objects)

    def put(self, num: int, body: bytes) -> int:
        self.objects[num - 1] = body
        return num

    def add(self, body: bytes) -> int:
        return self.put(self.reserve(), body)

    def stream(self, body: bytes, extra: str = "") -> int:
        # Uncompressed on purpose: a reviewer can read these fixtures.
        return self.add(
            b"<</Length " + str(len(body)).encode() + extra.encode() + b">>\n"
            b"stream\n" + body + b"\nendstream"
        )

    def build(self, root: int, info: int | None = None, doc_id: str = "ABYOCORPUS0001") -> bytes:
        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0] * (len(self.objects) + 1)
        for i, body in enumerate(self.objects, start=1):
            if body is None:
                raise AssertionError(f"object {i} was reserved but never written")
            offsets[i] = len(out)
            out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"

        xref_at = len(out)
        out += f"xref\n0 {len(self.objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for i in range(1, len(self.objects) + 1):
            out += f"{offsets[i]:010d} 00000 n \n".encode()
        trailer = f"<</Size {len(self.objects) + 1}/Root {root} 0 R"
        if info is not None:
            trailer += f"/Info {info} 0 R"
        trailer += f"/ID [<{doc_id}> <{doc_id}>]>>"
        out += b"trailer\n" + trailer.encode() + b"\n"
        out += f"startxref\n{xref_at}\n%%EOF\n".encode()
        return bytes(out)


def pdf_string(text: str) -> bytes:
    """A PDF literal string, with the three characters that need it escaped.

    Backslash first, so the escapes it introduces are not escaped again.
    """
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return b"(" + escaped.encode("ascii") + b")"


def helvetica(pdf: Pdf) -> int:
    return pdf.add(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>")


def gray_pixel(pdf: Pdf, value: int = 0) -> int:
    """A 1x1 DeviceGray image — scaled up, it is a solid rectangle."""
    return pdf.stream(
        bytes([value]),
        "/Type/XObject/Subtype/Image/Width 1/Height 1"
        "/ColorSpace/DeviceGray/BitsPerComponent 8",
    )


def photo(pdf: Pdf) -> int:
    """A 4x4 RGB block that is obviously not blank, standing in for a photo."""
    rows = []
    for y in range(4):
        for x in range(4):
            rows += [40 + 50 * x, 90 + 30 * y, 200 - 40 * x]
    return pdf.stream(
        bytes(rows),
        "/Type/XObject/Subtype/Image/Width 4/Height 4"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )


def black_block(pdf: Pdf) -> int:
    """A 4x4 all-black RGB image: an image region whose pixels have been
    rewritten, which is what a correct redaction of a photo looks like."""
    return pdf.stream(
        bytes([0] * 48),
        "/Type/XObject/Subtype/Image/Width 4/Height 4"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )


def scan_page(pdf: Pdf) -> int:
    """A 4x4 mostly-white RGB image: a page scan. Nearly white on
    purpose — it is what separates "the document" from "a blackout"."""
    rows = []
    for y in range(4):
        for x in range(4):
            dark = (x, y) == (1, 2)
            rows += [30, 30, 30] if dark else [245, 244, 240]
    return pdf.stream(
        bytes(rows),
        "/Type/XObject/Subtype/Image/Width 4/Height 4"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )


def page(
    pdf: Pdf,
    content: bytes,
    *,
    font: int | None = None,
    xobjects: dict[str, int] | None = None,
    shadings: dict[str, int] | None = None,
    annots: str = "",
    extra_page: str = "",
) -> tuple[int, int]:
    """Build a one-page document and return (catalog, page)."""
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(content)

    resources = "<<"
    if font is not None:
        resources += f"/Font<</F1 {font} 0 R>>"
    if xobjects:
        resources += "/XObject<<" + "".join(f"/{k} {v} 0 R" for k, v in xobjects.items()) + ">>"
    if shadings:
        resources += "/Shading<<" + "".join(f"/{k} {v} 0 R" for k, v in shadings.items()) + ">>"
    resources += ">>"

    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R"
            f"/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources {resources}"
            f"/Contents {contents} 0 R{annots}{extra_page}>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    catalog = pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode())
    return catalog, page_num


def text_op(x: int, y: int, body: str, *, size: int = 14, rgb: str = "0 0 0", tr: int | None = None) -> bytes:
    pre = f"{rgb} rg\n" if rgb else ""
    mode = f"{tr} Tr\n" if tr is not None else ""
    return (
        f"BT\n{pre}{mode}/F1 {size} Tf\n1 0 0 1 {x} {y} Tm\n({body}) Tj\nET\n"
    ).encode()


def black_rect(x: int, y: int, w: int, h: int) -> bytes:
    return f"0 0 0 rg\n{x} {y} {w} {h} re f\n".encode()


# --------------------------------------------------------------------------
# One builder per case. Each returns the complete file bytes.
# --------------------------------------------------------------------------

def case_01_path_over_text() -> bytes:
    pdf = Pdf()
    f = helvetica(pdf)
    body = text_op(72, 700, SECRET) + black_rect(68, 694, 290, 26)
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_02_image_over_text() -> bytes:
    pdf = Pdf()
    f = helvetica(pdf)
    img = gray_pixel(pdf, 0)
    body = (
        text_op(72, 700, SECRET)
        + b"q\n290 0 0 26 68 694 cm\n/Im0 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_03_black_square_annot() -> bytes:
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/Square/Rect[68 694 358 720]"
        b"/IC[0 0 0]/C[0 0 0]/CA 1/F 4>>"
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_04_unapplied_redact_annot() -> bytes:
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/Redact/Rect[68 694 358 720]"
        b"/QuadPoints[68 720 358 720 68 694 358 694]"
        b"/IC[0 0 0]/F 4/Subj(redact pending)>>"
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_05_white_text() -> bytes:
    pdf = Pdf()
    f = helvetica(pdf)
    body = text_op(72, 760, "Public part of the document") + text_op(
        72, 700, SECRET, rgb="1 1 1"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_06_ocr_layer_under_box() -> bytes:
    """A scan with an invisible OCR layer, blacked out with a rectangle."""
    pdf = Pdf()
    f = helvetica(pdf)
    img = photo(pdf)
    body = (
        b"q\n451 0 0 200 72 620 cm\n/Im0 Do\nQ\n"
        + text_op(80, 700, SECRET, tr=3)
        + black_rect(76, 694, 290, 26)
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_07_black_rect_over_photo() -> bytes:
    pdf = Pdf()
    f = helvetica(pdf)
    img = photo(pdf)
    body = (
        text_op(72, 780, "Photo with a face blacked out")
        + b"q\n300 0 0 200 72 540 cm\n/Im0 Do\nQ\n"
        + black_rect(140, 620, 90, 70)
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_08_incremental_old_revision() -> bytes:
    """Redacted properly, then saved incrementally over a version that
    still carries the secret — the earlier revision stays in the file."""
    pdf = Pdf()
    f = helvetica(pdf)
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    old_content = pdf.stream(text_op(72, 700, SECRET))
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>>>/Contents {old_content} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    catalog = pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode())
    base = pdf.build(catalog)

    # Incremental update: the content stream object is rewritten with the
    # text gone and a burnt-in black bar, and only that object is appended.
    new_body = text_op(72, 760, "Public part of the document") + black_rect(68, 694, 290, 26)
    replacement = (
        b"<</Length " + str(len(new_body)).encode() + b">>\nstream\n" + new_body + b"\nendstream"
    )
    out = bytearray(base)
    offset = len(out)
    out += f"{old_content} 0 obj\n".encode() + replacement + b"\nendobj\n"
    prev = base.rfind(b"startxref")
    prev_xref = int(base[prev + len(b"startxref"):].split()[0])
    xref_at = len(out)
    out += b"xref\n"
    out += f"{old_content} 1\n".encode()
    out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<</Size {old_content + 1}/Root {catalog} 0 R/Prev {prev_xref}"
        f"/ID [<ABYOCORPUS0001> <ABYOCORPUS0002>]>>\n"
    ).encode()
    out += f"startxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)


def case_09_actualtext_leftover() -> bytes:
    """Redacted on the page, but the tag tree still spells it out."""
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 760, "Public part of the document")
        + f"/Span <</ActualText ({SECRET})>> BDC\n".encode()
        + black_rect(68, 694, 290, 26)
        + b"EMC\n"
    )
    root, _ = page(pdf, body, font=f, extra_page="/StructParents 0")
    return pdf.build(root)


def case_10_partial_cover() -> bytes:
    pdf = Pdf()
    f = helvetica(pdf)
    # The bar stops short, so the tail of the name stays on the page.
    body = text_op(72, 700, SECRET) + black_rect(68, 694, 120, 26)
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_11_metadata_author_path() -> bytes:
    pdf = Pdf()
    f = helvetica(pdf)
    body = text_op(72, 760, "Public part of the document") + black_rect(68, 694, 290, 26)
    root, _ = page(pdf, body, font=f)
    # Escaped through pdf_string, not written by hand: a literal
    # backslash in a PDF string is "\\", and the hand-written version
    # of this line lost every separator ("\s" is not an escape, so the
    # backslash was dropped) and turned "\2026" into an octal character
    # code. The path has to arrive intact or the fixture is not testing
    # a path at all.
    creator = pdf_string("\\\\fileserver\\share\\private\\2026\\draft-with-names.docx")
    info = pdf.add(
        b"<</Author(Yamada Taro)/Title(disclosure draft)"
        b"/Creator" + creator +
        b"/CreationDate(D:20260101000000Z)/ModDate(D:20260101000000Z)>>"
    )
    return pdf.build(root, info=info)


def case_14_black_band_normal() -> bytes:
    """A normal document: a black heading band with white text ON it, and
    filled table cells. Nothing is hidden. Must not be flagged."""
    pdf = Pdf()
    f = helvetica(pdf)
    body = bytearray()
    body += black_rect(60, 770, 475, 34)
    # Drawn AFTER the band, so it is the visible heading.
    body += text_op(72, 780, "DISCLOSURE DECISION NOTICE", size=16, rgb="1 1 1")
    body += b"0.85 0.85 0.85 rg\n60 700 475 24 re f\n"
    body += text_op(72, 707, "Item | Value | Note", rgb="0 0 0")
    body += b"0 0 0 RG\n0.5 w\n60 700 m 535 700 l S\n"
    body += text_op(72, 660, "Ordinary body text that is meant to be read.")
    root, _ = page(pdf, bytes(body), font=f)
    return pdf.build(root)


def case_15_scanned_image_only() -> bytes:
    """A paper scan: one image over the whole page, no text layer.

    The image covers the entire CropBox on purpose. A10 only reports a
    text-free page when an image covers at least
    SPEC.imageOnlyPage.minCropBoxCoverage of it, and a scan off the
    glass is full-bleed; an inset image here would have made the fixture
    disagree with the rule it is meant to exercise.
    """
    pdf = Pdf()
    img = photo(pdf)
    body = f"q\n{PAGE_W} 0 0 {PAGE_H} 0 0 cm\n/Im0 Do\nQ\n".encode("ascii")
    root, _ = page(pdf, body, xobjects={"Im0": img})
    return pdf.build(root)


def case_90_generated_correct_redaction() -> bytes:
    """A stand-in for #12/#13: the text is genuinely gone from the content
    stream and a black bar is burnt in. Used only to check this side does
    not cry wolf; the real articles come from Acrobat and the editor."""
    pdf = Pdf()
    f = helvetica(pdf)
    body = text_op(72, 760, "Public part of the document") + black_rect(68, 694, 290, 26)
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_91_image_pixels_rewritten() -> bytes:
    """The A6 rule's own negative test: the pixels under the bar were
    rewritten to black AND a bar was drawn, which is what both Acrobat
    and the editor produce. A6 must stay quiet. Without this case the
    pixel condition could be dropped and the suite would not notice."""
    pdf = Pdf()
    f = helvetica(pdf)
    pic = photo(pdf)
    blanked = black_block(pdf)
    body = (
        text_op(72, 780, "Photo with the face removed")
        # The photo, left visible.
        + b"q\n300 0 0 120 72 620 cm\n/Im0 Do\nQ\n"
        # The region that was redacted: pixels already black...
        + b"q\n90 0 0 70 140 540 cm\n/Im1 Do\nQ\n"
        # ...and the bar drawn over it, as a redaction tool would.
        + black_rect(140, 540, 90, 70)
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": pic, "Im1": blanked})
    return pdf.build(root)


def case_92_scanned_with_ocr_layer() -> bytes:
    """A scan with an OCR layer and nothing redacted — the commonest
    shape of document this tool will be handed, and the one a naive
    checker ruins itself on. The page image is opaque and covers
    everything, so treating any opaque image as a blackout reports every
    scanned-and-OCRd PDF as leaking. It must come back clean."""
    pdf = Pdf()
    f = helvetica(pdf)
    img = scan_page(pdf)
    body = (
        b"q\n451 0 0 638 72 102 cm\n/Im0 Do\nQ\n"
        + text_op(90, 700, "Minutes of the meeting held on 2026-04-01", tr=3)
        + text_op(90, 680, "Present: the committee and three observers", tr=3)
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


# ---------------------------------------------------------------- A11

_PW_PAD = bytes(
    [
        0x28, 0xBF, 0x4E, 0x5E, 0x4E, 0x75, 0x8A, 0x41,
        0x64, 0x00, 0x4E, 0x56, 0xFF, 0xFA, 0x01, 0x08,
        0x2E, 0x2E, 0x00, 0xB6, 0xD0, 0x68, 0x3E, 0x80,
        0x2F, 0x0C, 0xA9, 0xFE, 0x64, 0x53, 0x69, 0x7A,
    ]
)
_ENC_ID = bytes.fromhex("0123456789abcdef0123456789abcdef")
_ENC_P = -1


def _rc4(key: bytes, data: bytes) -> bytes:
    box = list(range(256))
    j = 0
    for i in range(256):
        j = (j + box[i] + key[i % len(key)]) & 0xFF
        box[i], box[j] = box[j], box[i]
    out = bytearray()
    i = j = 0
    for byte in data:
        i = (i + 1) & 0xFF
        j = (j + box[i]) & 0xFF
        box[i], box[j] = box[j], box[i]
        out.append(byte ^ box[(box[i] + box[j]) & 0xFF])
    return bytes(out)


def _encrypted_pdf(user_pw: str, owner_pw: str) -> bytes:
    """A readable page behind RC4, 40-bit, revision 2.

    Written out by hand because the point of these two files is the
    encryption dictionary itself, and the whole document has to be
    encrypted with a key derived the way the PDF standard security
    handler derives it. Algorithm numbers below are the ones in the
    specification's security section.
    """

    def padded(pw: str) -> bytes:
        return (pw.encode("latin-1") + _PW_PAD)[:32]

    # Algorithm 3 — /O from the owner password.
    owner_entry = _rc4(hashlib.md5(padded(owner_pw)).digest()[:5], padded(user_pw))
    # Algorithm 2 — the file key.
    digest = hashlib.md5()
    digest.update(padded(user_pw))
    digest.update(owner_entry)
    digest.update(struct.pack("<i", _ENC_P))
    digest.update(_ENC_ID)
    file_key = digest.digest()[:5]
    # Algorithm 4 — /U, revision 2.
    user_entry = _rc4(file_key, _PW_PAD)

    def object_key(num: int, gen: int) -> bytes:
        extra = bytes(
            [num & 0xFF, (num >> 8) & 0xFF, (num >> 16) & 0xFF, gen & 0xFF, (gen >> 8) & 0xFF]
        )
        return hashlib.md5(file_key + extra).digest()[:10]

    body = b"BT /F1 12 Tf 72 700 Td (Encrypted but readable) Tj ET\n"
    stream = _rc4(object_key(5, 0), body)

    objects = {
        1: b"<</Type/Catalog/Pages 2 0 R>>",
        2: b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        3: f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
        "/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>".encode("ascii"),
        4: b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        5: b"<</Length "
        + str(len(stream)).encode("ascii")
        + b">>\nstream\n"
        + stream
        + b"\nendstream",
        6: b"<</Filter/Standard/V 1/R 2/O <"
        + owner_entry.hex().encode("ascii")
        + b">/U <"
        + user_entry.hex().encode("ascii")
        + b">/P "
        + str(_ENC_P).encode("ascii")
        + b">>",
    }

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += str(num).encode("ascii") + b" 0 obj\n" + objects[num] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode("ascii") + b"\n"
    out += b"0000000000 65535 f \n"
    for num in sorted(objects):
        out += f"{offsets[num]:010d} 00000 n \n".encode("ascii")
    ident = _ENC_ID.hex().encode("ascii")
    out += (
        b"trailer\n<</Size "
        + str(len(objects) + 1).encode("ascii")
        + b"/Root 1 0 R/Encrypt 6 0 R/ID[<"
        + ident
        + b"><"
        + ident
        + b">]>>\nstartxref\n"
        + str(xref).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(out)


def case_93_encrypted_opens_empty_password() -> bytes:
    """Owner password only: encrypted, and opens with no password at all.

    This is the shape A11 must stay quiet about. It is everywhere — a
    great many published PDFs carry owner-password encryption to mark
    them no-printing or no-copying while opening for any reader — so a
    checker that reports every /Encrypt puts "needs a look" on documents
    it read straight through, and a verdict that never varies is one
    people learn to skip past.
    """
    return _encrypted_pdf(user_pw="", owner_pw="ownersecret")


def case_94_encrypted_needs_password() -> bytes:
    """A user password we do not have: nothing inside can be checked.

    The A11 case proper. Every content check is unrunnable here, which
    has to be reported as unchecked rather than passed.
    """
    return _encrypted_pdf(user_pw="usersecret", owner_pw="ownersecret")


def _annotation_over_text(descriptor: str) -> bytes:
    """One annotation laid over live text, described by `descriptor`."""
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(f"<</Type/Annot/Subtype/{descriptor}/F 4>>".encode("ascii"))
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


# A Highlight with no /QuadPoints is not a Highlight: the library drops
# such an annotation from getAnnotations() altogether, and a fixture
# without them silently tests nothing. Real ones always carry them.
_QUAD = "/QuadPoints[68 720 358 720 68 694 358 694]"


def case_95_grey_square_annot() -> bytes:
    """An opaque but light annotation over text.

    It conceals, and it comes off with the annotation, so the text is
    recoverable — but nothing here says the author meant it as a
    redaction, which is why this is a look-at rather than a failure.
    """
    return _annotation_over_text("Square/Rect[68 694 358 720]/IC[0.6 0.6 0.6]/C[0.6 0.6 0.6]/CA 1")


def case_96_yellow_highlight_annot() -> bytes:
    """An ordinary yellow highlighter over text: not a redaction at all.

    Nothing is hidden, so nothing is reported. This file is here as a
    guard: treating every Highlight over text as concealment put a
    failure on every highlighted document that passed through, which is
    the kind of false positive that teaches people the tool is wrong.
    """
    return _annotation_over_text(f"Highlight/Rect[68 694 358 720]{_QUAD}/C[1 1 0]/CA 1")


def case_97_black_highlight_annot() -> bytes:
    """The highlighter used as a marker pen to black text out.

    Dark and over text, so this is the failure A3 names, even though the
    subtype is the same as the harmless case above.
    """
    return _annotation_over_text(f"Highlight/Rect[68 694 358 720]{_QUAD}/C[0 0 0]/CA 1")


def case_98_highlight_dark_appearance() -> bytes:
    """A /Highlight that says yellow and draws opaque black.

    The dictionary and the appearance stream are allowed to disagree,
    and /AP is what the reader sees. Grading this annotation by /C would
    read it as an ordinary highlighter and let it through, so this file
    is the guard on judging the appearance instead.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    appearance = pdf.stream(
        b"0 0 0 rg\n68 694 290 26 re\nf\n",
        "/Type/XObject/Subtype/Form/BBox[68 694 358 720]",
    )
    annot = pdf.add(
        f"<</Type/Annot/Subtype/Highlight/Rect[68 694 358 720]{_QUAD}"
        f"/C[1 1 0]/CA 1/F 4/AP<</N {appearance} 0 R>>>>".encode("ascii")
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_99_form_xobject_blackout() -> bytes:
    """The blackout is drawn inside a Form XObject with its own /Matrix.

    The form's /Matrix is the only thing that puts the rectangle over
    the text, and the library reports it in the
    paintFormXObjectBegin operator rather than as a `cm`. An
    implementation that walks the operator list without handling that
    op computes the rectangle at the form's own origin, finds it covers
    nothing, and reports a document whose text is fully extractable
    under an opaque black box as having no problem.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    form = pdf.stream(
        b"0 0 0 rg\n0 0 290 26 re\nf\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 290 26]/Matrix[1 0 0 1 68 694]",
    )
    body = text_op(72, 700, SECRET) + b"q\n/Fm0 Do\nQ\n"
    root, _ = page(pdf, body, font=f, xobjects={"Fm0": form})
    return pdf.build(root)


def case_100_annotation_scaled_appearance() -> bytes:
    """A black appearance scaled from a 10x10 /BBox onto a wide /Rect.

    Nothing in the annotation dictionary says "black" — no /C, no /IC —
    and the appearance stream is drawn in its own small space, to be
    fitted to /Rect by the reader. Measuring the appearance without that
    fitting puts the bar at a tenth of its size in the corner of the
    page, where it covers nothing, and the document reads as clean with
    an opaque black bar across a line of live text.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    appearance = pdf.stream(
        b"0 0 0 rg\n0 0 10 10 re\nf\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 10 10]",
    )
    annot = pdf.add(
        f"<</Type/Annot/Subtype/Highlight/Rect[68 694 358 720]{_QUAD}"
        f"/CA 1/F 4/AP<</N {appearance} 0 R>>>>".encode("ascii")
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_101_stroke_colour_blackout() -> bytes:
    """A black bar drawn as a wide stroke, with the fill colour set light.

    PDF keeps the stroke colour (RG) and the fill colour (rg) apart. An
    implementation that reads the fill colour for a stroke grades this
    bar by the white fill and softens a covered line of text to "needs a
    look" when it is squarely a failure.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = text_op(72, 700, SECRET) + (
        b"1 1 1 rg\n"
        b"0 0 0 RG\n26 w\n"
        b"72 707 m\n362 707 l\nS\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_102_white_stroke_over_text() -> bytes:
    """The same shape in reverse: a white stroke, fill left at the default.

    The cover hides the text from a reader but is not dark, so it earns
    a look rather than a failure. Reading the fill colour here reports a
    failure instead, which is the same bug pointing the other way.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = text_op(72, 700, SECRET) + b"1 1 1 RG\n26 w\n72 707 m\n362 707 l\nS\n"
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_103_shading_cover() -> bytes:
    """A black-to-black gradient clipped over the text.

    A shading's colour is not in the operator list, so no implementation
    can call this a dark cover from the content stream alone. What it
    must not do is lose it: the cover has to surface as something the
    reader is told to look at, not vanish between the checks.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    shading = pdf.add(
        b"<</ShadingType 2/ColorSpace/DeviceRGB/Coords[72 694 362 720]"
        b"/Function<</FunctionType 2/Domain[0 1]/C0[0 0 0]/C1[0 0 0]/N 1>>"
        b"/Extend[true true]>>"
    )
    body = text_op(72, 700, SECRET) + b"q\n72 694 290 26 re\nW n\n/Sh0 sh\nQ\n"
    root, _ = page(pdf, body, font=f, shadings={"Sh0": shading})
    return pdf.build(root)


def case_104_type3_glyph_cover() -> bytes:
    """The cover is a Type 3 glyph whose procedure fills a black box.

    A known gap on the abyo.net checker, kept here so it stays visible
    rather than being forgotten: the library does not put a Type 3
    glyph's drawing in the page's operator list, and exposes it only
    while rendering, so a walker of that list cannot see this cover at
    all. The manifest states the true expectation (fail); the
    acceptance suite lists this file as a known gap so the run does not
    go green over it.

    An implementation that parses content streams itself can expand the
    /CharProcs entry and should get this right.
    """
    pdf = Pdf()
    helv = helvetica(pdf)
    charproc = pdf.stream(b"290 0 0 0 290 26 d1\n0 0 290 26 re\nf\n")
    type3 = pdf.add(
        b"<</Type/Font/Subtype/Type3/FontBBox[0 0 290 26]/FontMatrix[1 0 0 1 0 0]"
        b"/CharProcs<</box " + str(charproc).encode("ascii") + b" 0 R>>"
        b"/Encoding<</Type/Encoding/Differences[97/box]>>"
        b"/FirstChar 97/LastChar 97/Widths[290]>>"
    )
    body = text_op(72, 700, SECRET) + b"BT\n/F2 1 Tf\n1 0 0 1 68 694 Tm\n(a) Tj\nET\n"
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {helv} 0 R/F2 {type3} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode("ascii"),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode("ascii"))
    root = pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode("ascii"))
    return pdf.build(root)


def case_105_image_mostly_off_page() -> bytes:
    """A page with no text and an image hanging off the top edge.

    The image is the size of the page and placed so only about a tenth
    of it lands on the paper. A10's coverage is measured against the
    CropBox after clipping, so this is not a scan and nothing is
    reported. Measuring the union bounding box without clipping gives
    1.0 here and reports review on a page that is almost entirely
    blank.
    """
    pdf = Pdf()
    img = photo(pdf)
    body = f"q\n{PAGE_W} 0 0 {PAGE_H} 0 {PAGE_H - 84} cm\n/Im0 Do\nQ\n".encode("ascii")
    root, _ = page(pdf, body, xobjects={"Im0": img})
    return pdf.build(root)


def case_106_images_in_opposite_corners() -> bytes:
    """A text-free page with a small image in two opposite corners.

    Neither covers any meaningful part of the paper, so this is not a
    scan. The first answer to A10 measured the union *bounding box* of
    the page's images, which here spans almost the whole sheet: this
    page measured 0.94 covered and was reported as a scan nobody could
    read. Coverage is the sum of the clipped image areas instead.
    """
    pdf = Pdf()
    img = photo(pdf)
    body = (
        b"q\n60 0 0 60 20 20 cm\n/Im0 Do\nQ\n"
        + f"q\n60 0 0 60 {PAGE_W - 80} {PAGE_H - 80} cm\n/Im0 Do\nQ\n".encode("ascii")
    )
    root, _ = page(pdf, body, xobjects={"Im0": img})
    return pdf.build(root)


def case_107_annotation_text() -> bytes:
    """A note left in an annotation's /Contents, over nothing.

    Annotation text travels with the file without being part of the
    page, so A9 reports it. The annotation here covers no text and is
    not a cover candidate, so A9 is the only thing this file tests.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/Square/Rect[400 100 500 140]"
        b"/IC[1 1 0]/C[1 1 0]/CA 1/F 4"
        b"/Contents(reviewer: do not release the Yamada paragraph)>>"
    )
    root, _ = page(pdf, text_op(72, 700, "Public part of the document"), font=f,
                   annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_108_broken_revision_chain() -> bytes:
    """Case 08 again, with the trailer's /Prev entry broken.

    The earlier revision's bytes are untouched and still hold the name;
    only the record pointing at them is damaged. A reader sees the
    redacted page either way.

    Counting revisions as min(%%EOF markers, /Prev entries + 1) — which
    is what keeps a linearized file from reading as an incremental save
    — made this file count as a single revision, so nothing looked at
    the earlier bytes and it came back clean. The surplus of %%EOF
    markers over what the chain accounts for is the signal.
    """
    data = case_08_incremental_old_revision()
    return re.sub(rb"/Prev\s+\d+", b"/Xrev 0", data)


def case_109_stray_eof_marker() -> bytes:
    """A correct, single-revision redaction with one stray %%EOF appended.

    The guard on the surplus-%%EOF signal from case 108. There is no
    earlier revision here: the extra marker is the kind of thing that
    turns up in a stream's binary data. Reading the bytes before the
    first marker therefore recovers the document's own visible text, and
    an implementation that reports that as recovered from an earlier
    revision puts a failure on a correctly redacted file — which is the
    one thing this checker must never do.
    """
    return case_90_generated_correct_redaction() + b"\n%%EOF\n"


def _incremental_info_save(stream_body: bytes, stream_extra: str = "") -> bytes:
    """A one-page document, then an appended revision adding only /Info."""
    pdf = Pdf()
    f = helvetica(pdf)
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    content = pdf.stream(stream_body, stream_extra)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>>>/Contents {content} 0 R>>"
        ).encode("ascii"),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode("ascii"))
    catalog = pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode("ascii"))
    base = pdf.build(catalog)

    info_num = pdf.reserve()
    out = bytearray(base)
    offset = len(out)
    out += (
        f"{info_num} 0 obj\n".encode("ascii")
        + b"<</Title(quarterly report)/Author(communications team)>>"
        + b"\nendobj\n"
    )
    prev = base.rfind(b"startxref")
    prev_xref = int(base[prev + len(b"startxref"):].split()[0])
    xref_at = len(out)
    out += b"xref\n"
    out += f"{info_num} 1\n".encode("ascii")
    out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<</Size {info_num + 1}/Root {catalog} 0 R/Info {info_num} 0 R"
        f"/Prev {prev_xref}/ID [<ABYOCORPUS0001> <ABYOCORPUS0002>]>>\n"
    ).encode("ascii")
    out += f"startxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return bytes(out)


def case_111_compressed_earlier_revision() -> bytes:
    """Case 110 with the earlier revision's content stream compressed.

    Which is what a real editor produces. Text extraction reads only
    uncompressed content, so "nothing hidden in the earlier revision" is
    not something an implementation can claim here — it read nothing
    there at all. A look, with the reason given, rather than a pass.
    """
    return _incremental_info_save(
        zlib.compress(text_op(72, 700, "Public part of the document")),
        "/Filter/FlateDecode",
    )


def case_110_metadata_only_incremental_save() -> bytes:
    """A legitimate incremental save that changed only the document info.

    Nothing was redacted and nothing is hidden: the earlier revision
    holds exactly the text that is still on the page. An implementation
    that reports whatever it recovers from the earlier bytes, without
    checking it against what is visible now, calls this a failure — and
    this is an ordinary file that any editor produces when it saves a
    title or an author over an existing document.
    """
    return _incremental_info_save(text_op(72, 700, "Public part of the document"))


def case_112_unopenable_with_structure_text() -> bytes:
    """Case 09, with /Root pointing at an object that is not there.

    The file will not open, so none of the page checks can run. The
    name is still sitting in an /ActualText entry where `strings` would
    find it, and A8 and A9 read the file's bytes rather than its page
    tree — so giving up on the whole document because it will not open
    leaves that name unmentioned. A look rather than a failure: with no
    page to compare against, a tagged string cannot be told from a
    caption.
    """
    return re.sub(rb"/Root\s+\d+\s+0\s+R", b"/Root 9999 0 R",
                  case_09_actualtext_leftover())


def case_113_unopenable_with_document_info() -> bytes:
    """Case 11, made unopenable the same way as case 112.

    The author's name and the original file path are in /Info, and the
    document will not open. A9 reads the information dictionary out of
    the bytes on that path — standard keys and custom ones alike — so
    the same facts come out either way. Outlines, attachments by name,
    form values and hidden layers need the structure and stay
    unreported; the spec's onOpenFailure section says which is which.
    """
    return re.sub(rb"/Root\s+\d+\s+0\s+R", b"/Root 9999 0 R",
                  case_11_metadata_author_path())


def case_114_reflowed_between_revisions() -> bytes:
    """The same sentence, re-flowed across a line break by the next save.

    Nothing is hidden: every word recovered from the earlier revision is
    printed on the page. But the earlier revision wrote the sentence as
    one run, carrying its own spaces, while the current one writes two
    runs and the space at the line break belongs to neither — so the
    recovered string is not a substring of the text as extracted, and an
    implementation comparing them literally reports the document as
    hiding the very sentence printed on it.

    Text re-flowing between two saves is what editing a document does,
    which is why this is here.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    sentence = "Public part of the document"
    old_content = pdf.stream(text_op(72, 700, sentence))
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>>>/Contents {old_content} 0 R>>"
        ).encode("ascii"),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode("ascii"))
    catalog = pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode("ascii"))
    base = pdf.build(catalog)

    head, tail = "Public part of", "the document"
    new_body = (
        b"BT\n0 0 0 rg\n/F1 14 Tf\n16 TL\n1 0 0 1 72 700 Tm\n"
        + f"({head}) Tj\nT*\n({tail}) Tj\n".encode("ascii")
        + b"ET\n"
    )
    replacement = (
        b"<</Length " + str(len(new_body)).encode("ascii") + b">>\nstream\n"
        + new_body + b"\nendstream"
    )
    out = bytearray(base)
    offset = len(out)
    out += f"{old_content} 0 obj\n".encode("ascii") + replacement + b"\nendobj\n"
    prev = base.rfind(b"startxref")
    prev_xref = int(base[prev + len(b"startxref"):].split()[0])
    xref_at = len(out)
    out += b"xref\n"
    out += f"{old_content} 1\n".encode("ascii")
    out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<</Size {old_content + 1}/Root {catalog} 0 R/Prev {prev_xref}"
        f"/ID [<ABYOCORPUS0001> <ABYOCORPUS0002>]>>\n"
    ).encode("ascii")
    out += f"startxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return bytes(out)


def case_115_one_name_removed_from_a_sentence() -> bytes:
    """The realistic redaction: one name taken out of a sentence.

    The earlier revision reads "Yamada Taro lives in Yokohama"; the
    current one reads "Taro lives in Yokohama". Only the surname is
    hidden. Comparing whole strings reports the entire sentence as
    recovered, which quotes four words that are plainly on the page;
    comparing word by word reports the one word that is not.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    old_content = pdf.stream(text_op(72, 700, "Yamada Taro lives in Yokohama"))
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>>>/Contents {old_content} 0 R>>"
        ).encode("ascii"),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode("ascii"))
    catalog = pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode("ascii"))
    base = pdf.build(catalog)

    new_body = text_op(72, 700, "Taro lives in Yokohama")
    replacement = (
        b"<</Length " + str(len(new_body)).encode("ascii") + b">>\nstream\n"
        + new_body + b"\nendstream"
    )
    out = bytearray(base)
    offset = len(out)
    out += f"{old_content} 0 obj\n".encode("ascii") + replacement + b"\nendobj\n"
    prev = base.rfind(b"startxref")
    prev_xref = int(base[prev + len(b"startxref"):].split()[0])
    xref_at = len(out)
    out += b"xref\n"
    out += f"{old_content} 1\n".encode("ascii")
    out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<</Size {old_content + 1}/Root {catalog} 0 R/Prev {prev_xref}"
        f"/ID [<ABYOCORPUS0001> <ABYOCORPUS0002>]>>\n"
    ).encode("ascii")
    out += f"startxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return bytes(out)


def case_116_hidden_annotation_over_text() -> bytes:
    """A black Square annotation marked Hidden, over live text.

    /F bit 2 means the annotation is not drawn — not on screen, not on
    paper — so the text under it is in plain view and nothing has been
    concealed. An implementation that reads /Annots without checking the
    flags reports a failure here and quotes the text printed on the page
    as recovered from under a cover.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/Square/Rect[68 694 358 720]"
        b"/IC[0 0 0]/C[0 0 0]/CA 1/F 2>>"
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_117_short_name_inside_a_longer_word() -> bytes:
    """A two-letter surname in the earlier revision; a longer word now.

    The earlier revision holds "Li". The current page says "Lions Club",
    which contains those two letters without containing that name.
    Substring matching calls the name visible and reports nothing at
    all — a false clean, which is the verdict this tool must never get
    wrong. Matching on word boundaries reports it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    old_content = pdf.stream(text_op(72, 700, "Li"))
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>>>/Contents {old_content} 0 R>>"
        ).encode("ascii"),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode("ascii"))
    catalog = pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode("ascii"))
    base = pdf.build(catalog)

    new_body = text_op(72, 700, "Lions Club")
    replacement = (
        b"<</Length " + str(len(new_body)).encode("ascii") + b">>\nstream\n"
        + new_body + b"\nendstream"
    )
    out = bytearray(base)
    offset = len(out)
    out += f"{old_content} 0 obj\n".encode("ascii") + replacement + b"\nendobj\n"
    prev = base.rfind(b"startxref")
    prev_xref = int(base[prev + len(b"startxref"):].split()[0])
    xref_at = len(out)
    out += b"xref\n"
    out += f"{old_content} 1\n".encode("ascii")
    out += f"{offset:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<</Size {old_content + 1}/Root {catalog} 0 R/Prev {prev_xref}"
        f"/ID [<ABYOCORPUS0001> <ABYOCORPUS0002>]>>\n"
    ).encode("ascii")
    out += f"startxref\n{xref_at}\n%%EOF\n".encode("ascii")
    return bytes(out)


def case_118_line_initial_word_unchanged() -> bytes:
    """A word at the start of a line, unchanged by an incremental save.

    Nothing was redacted: the save changed only the document
    information, and "Yamada" is printed on the page. But the page text
    comes out of the extractor run by run, and joining the runs end to
    end puts the previous line's last letter directly in front of this
    word — "signed by" and "Yamada" become "signed byYamada" — so a
    word-boundary search finds no boundary and reports the name on the
    page as recovered from an earlier revision.

    The mirror image of a word the extractor splits across two runs,
    which the separated join breaks in half instead. Both joins have to
    be consulted.
    """
    body = (
        b"BT\n0 0 0 rg\n/F1 14 Tf\n16 TL\n1 0 0 1 72 700 Tm\n"
        b"(signed by) Tj\nT*\n(Yamada) Tj\nET\n"
    )
    return _incremental_info_save(body)


def case_119_hidden_redact_annotation() -> bytes:
    """An unapplied /Redact annotation marked Hidden.

    A redaction mark that was never applied is the problem whether or
    not anybody draws it, so the Hidden flag does not excuse it — where
    for any other subtype Hidden means nothing is covered (#116). This
    distinction was a bug introduced along with the Hidden check: it
    made an unapplied redaction vanish from the report.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/Redact/Rect[68 694 358 720]/IC[0 0 0]/CA 1/F 2>>"
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_120_vendor_subtype_with_appearance() -> bytes:
    """An annotation of an unknown subtype whose /AP paints a black box.

    A3 only looks at subtypes it knows, so this cover is invisible to
    it. It still has to be caught, and it is: A1 works from what was
    actually painted, whatever the annotation calls itself. The point of
    pinning it is that A3's subtype list is not load-bearing for safety
    — widening it should add precision, not close a hole.

    This file is also the tripwire for a leak the editor team found on
    their side: an evidence field passing a document's own /Subtype
    through verbatim. Nothing is reported for this subtype today, so
    nothing leaks; the moment somebody adds it to the concealing set,
    the saved-report guard sees the vendor name.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    appearance = pdf.stream(
        b"0 0 0 rg\n0 0 290 26 re\nf\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 290 26]",
    )
    annot = pdf.add(
        f"<</Type/Annot/Subtype/VendorBox/Rect[68 694 358 720]"
        f"/CA 1/F 4/AP<</N {appearance} 0 R>>>>".encode("ascii")
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_121_vendor_subtype_without_appearance() -> bytes:
    """The same unknown subtype with a colour but no appearance stream.

    A reader that does not recognise the subtype and has no appearance
    to draw shows nothing, so nothing is concealed and the text is in
    plain view. Deriving a cover from /IC for any subtype at all would
    report a failure here on a document with no problem.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/VendorBox/Rect[68 694 358 720]"
        b"/IC[0 0 0]/C[0 0 0]/CA 1/F 4>>"
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_122_stamp_colour_without_appearance() -> bytes:
    """A /Stamp with a black /IC and no appearance stream, over text.

    Only some subtypes get an appearance synthesised from the
    dictionary: a Square fills its /Rect, a Circle the ellipse inside
    it, a Highlight its quads. A Stamp with nothing but /IC draws
    nothing at all, so the text under it is in plain view — render the
    page and the area is blank. Treating the dictionary colour as a
    cover for any subtype reports a failure here on a document with no
    problem.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/Stamp/Rect[68 694 358 720]"
        b"/IC[0 0 0]/C[0 0 0]/CA 1/F 4>>"
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_123_compound_fill_two_bars() -> bytes:
    """Two black bars, far apart, painted as one path.

    `re re f` is a single fill operator holding two shapes. Judging it
    by the whole path's bounding box gives a box spanning the page that
    is about 5% filled, which is nowhere near rectangular enough to be a
    cover — so both lines of text came back clean while the rendered
    page shows them solidly blacked out. Each closed subpath has to be
    judged on its own.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 700, SECRET)
        + text_op(72, 300, SECRET)
        + b"0 0 0 rg\n68 694 290 26 re\n68 294 290 26 re\nf\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_124_frame_drawn_after_text() -> bytes:
    """A 5pt black border drawn round a paragraph, after the text.

    A stroke covers a ribbon along each segment, not the area the path
    encloses. Growing the whole path's box by half the pen width turns
    the frame into a solid block over everything inside it, and the text
    in the middle of an ordinary table cell reads as blacked out.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = text_op(72, 700, SECRET) + b"0 0 0 RG\n5 w\n60 680 320 60 re\nS\n"
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_125_frame_drawn_before_text() -> bytes:
    """The same border, drawn before the text rather than after.

    The same over-approximation, reaching a different check: with the
    frame treated as a solid block behind the words, black text on white
    paper reads as text the same colour as its background.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = b"0 0 0 RG\n5 w\n60 680 320 60 re\nS\n" + text_op(72, 700, SECRET)
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_126_white_box_with_black_border() -> bytes:
    """A white box with a black border, painted over the text.

    Not a frame: `B` fills and then strokes, so the white fill really
    does cover the words — opaque but not dark, which is a look rather
    than a failure. Here to keep the fix for #124 from going too far and
    dismissing a filled box because it also has a border.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 700, SECRET)
        + b"1 1 1 rg\n0 0 0 RG\n5 w\n60 680 320 60 re\nB\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_127_appearance_white_then_black() -> bytes:
    """An annotation appearance that paints white and then black.

    The first thing drawn inside the appearance covers nothing — it is
    the background. Stopping at the first cover found inside an
    appearance grades the annotation by the white one and reports a look
    where the black bar on top of it is a failure.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    appearance = pdf.stream(
        b"1 1 1 rg\n0 0 290 26 re\nf\n0 0 0 rg\n0 0 290 26 re\nf\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 290 26]",
    )
    annot = pdf.add(
        f"<</Type/Annot/Subtype/Square/Rect[68 694 358 720]"
        f"/CA 1/F 4/AP<</N {appearance} 0 R>>>>".encode("ascii")
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_128_text_clipped_away() -> bytes:
    """A line of text parked outside the clip, so none of it is drawn.

    Every pixel of that line is blank and the name comes straight out of
    the file. The clip operator arrives BEFORE the path it applies to,
    which is the opposite of what this implementation assumed, so the
    clip was never applied to anything and this branch of A4 could not
    fire at all — verified against the whole corpus: no file produced it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = b"q\n60 100 320 40 re\nW n\n" + text_op(72, 700, SECRET) + b"Q\n"
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_129_text_in_a_clip_gap() -> bytes:
    """Text in the gap between two clip rectangles.

    The clip's hull covers the gap, so testing a run against the hull
    says it is inside the clip and visible. It is not: a reader sees
    nothing of it. Each piece of the clip has to be tested.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"q\n68 760 290 26 re\n68 100 290 26 re\nW n\n"
        + text_op(72, 700, SECRET)
        + b"Q\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_130_text_inside_the_clip() -> bytes:
    """Ordinary clipped drawing: the text is inside the clip.

    The guard on the two above. Clipping is routine — every table cell
    and cropped image does it — so an implementation that starts
    applying clips must not start reporting the text inside them.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = b"q\n60 680 320 40 re\nW n\n" + text_op(72, 700, SECRET) + b"Q\n"
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_131_clip_trims_a_cover() -> bytes:
    """A black bar clipped to half the text it was drawn over.

    Half the name is still on the page, so this is a partial cover and
    not a full one. It takes a working clip to tell the difference: an
    implementation that never applies clips reads the bar at full width
    and calls the whole line covered.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 700, SECRET)
        + b"q\n68 694 145 26 re\nW n\n0 0 0 rg\n68 694 290 26 re\nf\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_132_undecodable_image_cover() -> bytes:
    """A cover whose image codec cannot be decoded here.

    The bar over the text claims to be JPEG 2000 and is not. Its own
    pixels cannot be read, so whether it is dark is unknown — and a
    check that could not run must never come back as a pass, so this is
    a look. It is also the only file that reaches the
    image-pixels-not-readable path at all, which is where the editor
    team found a skip entry carrying the document's own /Filter name.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    img = pdf.stream(
        b"\x00\x01\x02\x03not-a-real-jpeg2000-codestream",
        "/Type/XObject/Subtype/Image/Width 8/Height 8"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8/Filter/JPXDecode",
    )
    body = text_op(72, 700, SECRET) + b"q\n290 0 0 26 68 694 cm\n/Im0 Do\nQ\n"
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_133_bar_over_undecodable_image() -> bytes:
    """A dark bar over an image whose codec cannot be decoded here.

    A6 asks whether the image data under a cover is still intact. Here
    the image cannot be read at all, so the question is unanswerable
    and the answer is a look — never a pass. The only file that reaches
    that branch of A6.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    img = pdf.stream(
        b"\x00\x01\x02\x03not-a-real-jpeg2000-codestream",
        "/Type/XObject/Subtype/Image/Width 8/Height 8"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8/Filter/JPXDecode",
    )
    body = b"q\n290 0 0 60 68 660 cm\n/Im0 Do\nQ\n" + black_rect(68, 694, 290, 26)
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_134_broken_chain_and_compressed_revision() -> bytes:
    """Case 111 with its /Prev broken as well.

    The file shows more revisions than its chain accounts for, and the
    earlier bytes are compressed, so nothing legible comes out of them.
    "No earlier content found" is then not something this engine
    established, and the floor applies. The only file that reaches it.
    """
    return re.sub(rb"/Prev\s+\d+", b"/Xrev 0",
                  case_111_compressed_earlier_revision())


def case_135_ring_with_a_hole() -> bytes:
    """A black ring: two nested rectangles, even-odd fill, text in the hole.

    The inner subpath is a hole, so the outer paints only the band
    around it and the text in the middle is on paper. Taking each
    subpath as painted throughout makes the ring a solid block, and
    black text in the middle of a frame then reads as text the colour
    of its background.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"0 0 0 rg\n60 640 320 120 re\n100 680 240 40 re\nf*\n"
        + text_op(110, 694, SECRET)
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_136_ring_text_on_the_band() -> bytes:
    """The same ring with the text on the band rather than in the hole.

    The guard on #135: subtracting the hole must not lose the cover the
    ring really does paint.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = text_op(72, 750, SECRET) + (
        b"0 0 0 rg\n60 640 320 140 re\n100 680 240 40 re\nf*\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_137_nested_subpaths_same_winding() -> bytes:
    """Nested subpaths wound the same way, nonzero fill: no hole.

    Under the nonzero rule a nested subpath is a hole only when the
    windings cancel. These do not, so the shape is solid and the text
    inside it really is invisible. An implementation that treats any
    nesting as a hole misses this.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"0 0 0 rg\n60 640 320 120 re\n100 680 240 40 re\nf\n"
        + text_op(110, 694, SECRET)
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_138_pale_cover_over_part_of_a_line() -> bytes:
    """A pale opaque annotation overlapping the first third of a line.

    The ladder has two thresholds: a dark cover fails as soon as it
    covers a sliver, where a cover that is merely opaque earns a look
    only once it hides essentially all of a run. Using the sliver
    threshold for both reports an ordinary pale box that happens to
    overlap a line.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/Square/Rect[68 694 150 720]"
        b"/IC[0.6 0.6 0.6]/C[0.6 0.6 0.6]/CA 1/F 4>>"
    )
    root, _ = page(pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]")
    return pdf.build(root)


def case_139_seam_two_bars() -> bytes:
    """A line met in the middle by two abutting bars.

    Neither bar covers enough of the line on its own to count as having
    hidden it, so measuring cover by cover reports the line twice as
    "part of it still shows" when none of it does. Coverage is against
    the union of the covers drawn over the glyph.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 700, SECRET)
        + b"0 0 0 rg\n68 694 150 26 re\nf\n"
        + b"0 0 0 rg\n218 694 150 26 re\nf\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_140_seam_many_bars() -> bytes:
    """The same line under ten narrow abutting bars.

    Each covers about a tenth of it, under any sensible threshold, so
    cover by cover reports nothing at all — and every pixel of that line
    is black. The false clean the union rule exists for.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    bars = b""
    x = 68
    for _ in range(10):
        bars += f"0 0 0 rg\n{x} 694 24 26 re\nf\n".encode("ascii")
        x += 24
    root, _ = page(pdf, text_op(72, 700, SECRET) + bars, font=f)
    return pdf.build(root)


def _acroform_page(field_src: bytes, name: str) -> bytes:
    """A one-page document whose only annotation is one form field."""
    pdf = Pdf()
    f = helvetica(pdf)
    field = pdf.add(field_src)
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(text_op(72, 760, "Public part of the document"))
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>>>/Contents {contents} 0 R"
            f"/Annots[{field} 0 R]>>"
        ).encode("ascii"),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode("ascii"))
    root = pdf.add(
        (
            f"<</Type/Catalog/Pages {pages_num} 0 R"
            f"/AcroForm<</Fields[{field} 0 R]/DA(/Helv 10 Tf 0 g)"
            f"/DR<</Font<</Helv {f} 0 R>>>>>>>>"
        ).encode("ascii")
    )
    return pdf.build(root)


def case_141_form_field_value() -> bytes:
    """A name typed into a text field.

    The value travels with the file and is not on the page. Counting the
    fields that exist rather than reading what they hold says nothing
    about it — and the library's getFieldObjects() comes back empty for a
    file it cannot relate to an /AcroForm, so the value has to be read
    off the widget annotation.
    """
    return _acroform_page(
        b"<</Type/Annot/Subtype/Widget/FT/Tx/T(name)/Ff 0"
        b"/Rect[72 700 300 720]/F 4/DA(/Helv 10 Tf 0 g)"
        b"/V(Yamada Taro 1980-04-01)>>",
        "141",
    )


def case_142_form_field_array_value() -> bytes:
    """A multi-select choice field whose value is an ARRAY of strings.

    A reader that expects a string or a name finds nothing here, so a
    non-empty selection passes. The editor team found exactly this on
    their side.
    """
    return _acroform_page(
        b"<</Type/Annot/Subtype/Widget/FT/Ch/Ff 2097152/T(picks)"
        b"/Rect[72 700 300 720]/F 4/DA(/Helv 10 Tf 0 g)"
        b"/Opt[(Yamada)(Suzuki)(Tanaka)]/V[(Yamada)(Tanaka)]>>",
        "142",
    )


def case_143_empty_form_field() -> bytes:
    """A form field with no value at all.

    The guard on the two above: counting the fields that exist reports
    "form values: 1" on a blank form and tells the reader nothing about
    whether anybody filled it in.
    """
    return _acroform_page(
        b"<</Type/Annot/Subtype/Widget/FT/Tx/T(blank)/Ff 0"
        b"/Rect[72 700 300 720]/F 4/DA(/Helv 10 Tf 0 g)>>",
        "143",
    )


def case_144_many_clip_pieces() -> bytes:
    """Text in a gap between eighty clip pieces, inside their hull.

    The pieces sit in two bands, well above and well below the line, so
    the hull of the clip covers the line and no piece of it does. An
    implementation that caps the pieces it keeps and falls back to their
    hull calls the text inside the clip and reports nothing; one that
    tests the pieces sees that none of them reaches the text.

    Putting the text outside the hull as well — the first version of
    this file — cannot tell the two apart, because a hull engine also
    finds it outside and reports it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    rects = b""
    for band_y in (760, 120):
        placed = 0
        y = band_y
        while placed < 40:
            x = 68
            while x < 500 and placed < 40:
                rects += f"{x} {y} 20 8 re\n".encode("ascii")
                x += 26
                placed += 1
            y += 12 if band_y == 120 else -12
    body = b"q\n" + rects + b"W n\n" + text_op(72, 440, SECRET) + b"Q\n"
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


SENTENCE = "The applicant Yamada Taro of Yokohama filed the request on 2026-01-05"


def case_145_annotation_over_a_name() -> bytes:
    """A dark annotation over one name inside a long sentence.

    Coverage is per glyph, so the name is covered outright and the
    sentence around it is not. What the finding reports matters as much:
    quoting the whole sentence presents the words still printed on the
    page as recovered from under the cover.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/Square/Rect[140 697 235 709]"
        b"/IC[0 0 0]/C[0 0 0]/CA 1/F 4>>"
    )
    root, _ = page(
        pdf, text_op(72, 700, SENTENCE, size=10), font=f,
        annots=f"/Annots[{annot} 0 R]",
    )
    return pdf.build(root)


def case_146_invisible_text_under_a_name_bar() -> bytes:
    """A bar over one name of an invisible line.

    A5's half of the same thing: the line is drawn in render mode 3 and
    a bar covers one name in it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = text_op(72, 700, SENTENCE, size=10, tr=3) + b"0 0 0 rg\n140 697 95 12 re\nf\n"
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


CASES = {
    "01-path-over-text": case_01_path_over_text,
    "02-image-over-text": case_02_image_over_text,
    "03-black-square-annot": case_03_black_square_annot,
    "04-unapplied-redact-annot": case_04_unapplied_redact_annot,
    "05-white-text": case_05_white_text,
    "06-ocr-layer-under-box": case_06_ocr_layer_under_box,
    "07-black-rect-over-photo": case_07_black_rect_over_photo,
    "08-incremental-old-revision": case_08_incremental_old_revision,
    "09-actualtext-leftover": case_09_actualtext_leftover,
    "10-partial-cover": case_10_partial_cover,
    "11-metadata-author-path": case_11_metadata_author_path,
    "14-black-band-normal": case_14_black_band_normal,
    "15-scanned-image-only": case_15_scanned_image_only,
    "90-generated-correct-redaction": case_90_generated_correct_redaction,
    "91-image-pixels-rewritten": case_91_image_pixels_rewritten,
    "92-scanned-with-ocr-layer": case_92_scanned_with_ocr_layer,
    "93-encrypted-opens-empty-password": case_93_encrypted_opens_empty_password,
    "94-encrypted-needs-password": case_94_encrypted_needs_password,
    "95-grey-square-annot": case_95_grey_square_annot,
    "96-yellow-highlight-annot": case_96_yellow_highlight_annot,
    "97-black-highlight-annot": case_97_black_highlight_annot,
    "98-highlight-dark-appearance": case_98_highlight_dark_appearance,
    "99-form-xobject-blackout": case_99_form_xobject_blackout,
    "100-annotation-scaled-appearance": case_100_annotation_scaled_appearance,
    "101-stroke-colour-blackout": case_101_stroke_colour_blackout,
    "102-white-stroke-over-text": case_102_white_stroke_over_text,
    "103-shading-cover": case_103_shading_cover,
    "104-type3-glyph-cover": case_104_type3_glyph_cover,
    "105-image-mostly-off-page": case_105_image_mostly_off_page,
    "106-images-in-opposite-corners": case_106_images_in_opposite_corners,
    "107-annotation-text": case_107_annotation_text,
    "108-broken-revision-chain": case_108_broken_revision_chain,
    "109-stray-eof-marker": case_109_stray_eof_marker,
    "110-metadata-only-incremental-save": case_110_metadata_only_incremental_save,
    "111-compressed-earlier-revision": case_111_compressed_earlier_revision,
    "112-unopenable-with-structure-text": case_112_unopenable_with_structure_text,
    "113-unopenable-with-document-info": case_113_unopenable_with_document_info,
    "114-reflowed-between-revisions": case_114_reflowed_between_revisions,
    "115-one-name-removed-from-a-sentence": case_115_one_name_removed_from_a_sentence,
    "116-hidden-annotation-over-text": case_116_hidden_annotation_over_text,
    "117-short-name-inside-a-longer-word": case_117_short_name_inside_a_longer_word,
    "118-line-initial-word-unchanged": case_118_line_initial_word_unchanged,
    "119-hidden-redact-annotation": case_119_hidden_redact_annotation,
    "120-vendor-subtype-with-appearance": case_120_vendor_subtype_with_appearance,
    "121-vendor-subtype-without-appearance": case_121_vendor_subtype_without_appearance,
    "122-stamp-colour-without-appearance": case_122_stamp_colour_without_appearance,
    "123-compound-fill-two-bars": case_123_compound_fill_two_bars,
    "124-frame-drawn-after-text": case_124_frame_drawn_after_text,
    "125-frame-drawn-before-text": case_125_frame_drawn_before_text,
    "126-white-box-with-black-border": case_126_white_box_with_black_border,
    "127-appearance-white-then-black": case_127_appearance_white_then_black,
    "128-text-clipped-away": case_128_text_clipped_away,
    "129-text-in-a-clip-gap": case_129_text_in_a_clip_gap,
    "130-text-inside-the-clip": case_130_text_inside_the_clip,
    "131-clip-trims-a-cover": case_131_clip_trims_a_cover,
    "132-undecodable-image-cover": case_132_undecodable_image_cover,
    "133-bar-over-undecodable-image": case_133_bar_over_undecodable_image,
    "134-broken-chain-and-compressed-revision": case_134_broken_chain_and_compressed_revision,
    "135-ring-with-a-hole": case_135_ring_with_a_hole,
    "136-ring-text-on-the-band": case_136_ring_text_on_the_band,
    "137-nested-subpaths-same-winding": case_137_nested_subpaths_same_winding,
    "138-pale-cover-over-part-of-a-line": case_138_pale_cover_over_part_of_a_line,
    "139-seam-two-bars": case_139_seam_two_bars,
    "140-seam-many-bars": case_140_seam_many_bars,
    "141-form-field-value": case_141_form_field_value,
    "142-form-field-array-value": case_142_form_field_array_value,
    "143-empty-form-field": case_143_empty_form_field,
    "144-many-clip-pieces": case_144_many_clip_pieces,
    "145-annotation-over-a-name": case_145_annotation_over_a_name,
    "146-invisible-text-under-a-name-bar": case_146_invisible_text_under_a_name_bar,
}


def main() -> int:
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "files"
    )
    os.makedirs(outdir, exist_ok=True)
    for slug, builder in sorted(CASES.items()):
        data = builder()
        path = os.path.join(outdir, f"{slug}.pdf")
        with open(path, "wb") as fh:
            fh.write(data)
        print(f"  {slug}.pdf  {len(data):>7} bytes")
    print(f"{len(CASES)} files -> {outdir}")
    print("#12 (Acrobat) and #13 (abyo editor) must be produced by those applications.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

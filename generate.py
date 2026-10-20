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
import math
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
    colorspaces: dict[str, int] | None = None,
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
    if colorspaces:
        # An inline image cannot carry an indirect reference, so a
        # colour space with a function in it has to be a named page
        # resource - which is how a real file does it too.
        resources += (
            "/ColorSpace<<" + "".join(f"/{k} {v} 0 R" for k, v in colorspaces.items()) + ">>"
        )
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


def case_147_two_bands_over_every_glyph() -> bytes:
    """Two bars across a line, one near the top of the glyph box and one
    near the bottom, with a gap between them.

    Every glyph is under BOTH, so the union of two overlapping covers is
    what decides the threshold — and the union leaves a band showing, so
    this is a partial cover and not a full one.

    Found by mutation testing, not by reading: with one cover over a
    glyph the coverage comes from a fast path, and nothing in the corpus
    put a glyph under two covers at once with a partial total. The exact
    union code could be broken outright — made to report everything as
    fully covered — with every test still passing.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 700, SECRET)
        + b"0 0 0 rg\n68 705.6 290 5.6 re\nf\n"
        + b"0 0 0 rg\n68 697.2 290 5.6 re\nf\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_148_rotated_bar_over_text() -> bytes:
    """A black bar rotated twelve degrees over a line of text.

    The first fixture whose transform is not a translate or a scale. Up
    to here every cover was axis-aligned, so the matrix multiplication,
    the point transform and the rotating-calipers rectangularity could
    all be broken without a single test noticing — mutation testing
    found all three untouched by the corpus at once.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    angle = 12 * math.pi / 180
    cos = math.cos(angle)
    sin = math.sin(angle)
    body = (
        text_op(72, 700, SECRET)
        + f"q\n{cos:.6f} {sin:.6f} {-sin:.6f} {cos:.6f} 68 694 cm\n".encode("ascii")
        + b"0 0 0 rg\n0 0 300 26 re\nf\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_149_rotated_image_cover() -> bytes:
    """A black image, rotated, used as the cover over a line of text.

    Deciding whether an image's own pixels are dark means mapping the
    covered region back into the image, which needs the inverse of its
    matrix. Every image in the corpus up to here was placed by a scale
    and a translate, where the inverse's cross terms are zero — so that
    arithmetic could be wrong in several ways with nothing noticing.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    img = black_block(pdf)
    angle = 12 * math.pi / 180
    cos = math.cos(angle)
    sin = math.sin(angle)
    body = (
        text_op(72, 700, SECRET)
        + (
            f"q\n{300 * cos:.6f} {300 * sin:.6f} {-26 * sin:.6f} {26 * cos:.6f}"
            f" 68 694 cm\n"
        ).encode("ascii")
        + b"/Im0 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_150_triangular_clip() -> bytes:
    """A name in the corner a triangular clip's bounding box adds.

    The clip is the triangle (100,400) (400,400) (100,700); its box
    reaches (400,700) and the name sits at (320,650) — comfortably
    INSIDE the box and outside the triangle. Not one pixel of it is
    drawn. Standing a piece's bounding box in for the piece calls it
    visible and reports nothing.

    The name is short on purpose. The first version of this file used a
    long line starting at x=300, which ran past the box's right edge at
    x=400 — so an implementation testing glyph by glyph raised A4 for
    the glyphs beyond the edge and the file passed for a reason that had
    nothing to do with the triangle. The editor team caught that.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"q\n100 400 m\n400 400 l\n100 700 l\nh\nW n\n"
        + text_op(320, 650, "Yamada", size=10)
        + b"Q\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_151_triangular_clip_inside() -> bytes:
    """The same triangular clip with the text well inside it.

    The guard on #150: moving from boxes to regions must not start
    reporting the text a clip actually keeps.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"q\n100 400 m\n400 400 l\n100 700 l\nh\nW n\n"
        + text_op(110, 420, SECRET)
        + b"Q\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_152_annotation_author_and_rich_text() -> bytes:
    """A comment with an author (/T) and a rich-text body (/RC).

    /T is a person's name, travelling with the file and never drawn on
    the page, and /RC can say something /Contents does not. Reading only
    /Contents leaves the author unmentioned.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/Text/Rect[72 700 90 718]/F 4"
        b"/T(Yamada Taro)/Subj(2026 disclosure draft)"
        b"/RC(<?xml version=\"1.0\"?><body>remove the Suzuki paragraph</body>)"
        b"/Contents(remove the Suzuki paragraph)>>"
    )
    root, _ = page(
        pdf, text_op(72, 760, "Public part of the document"), font=f,
        annots=f"/Annots[{annot} 0 R]",
    )
    return pdf.build(root)


def _rotated_rect(cx: float, cy: float, w: float, h: float, degrees: float):
    """The four corners of a rectangle rotated about its centre."""
    angle = degrees * math.pi / 180
    cos = math.cos(angle)
    sin = math.sin(angle)
    return [
        (cx + dx * cos - dy * sin, cy + dx * sin + dy * cos)
        for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))
    ]


def _rotated_clip(text_x: float, text_y: float, word: str) -> bytes:
    """`word` drawn inside a clip that is one rotated rectangle."""
    corners = _rotated_rect(250, 550, 260, 120, 35)
    path = f"{corners[0][0]:.3f} {corners[0][1]:.3f} m\n" + "".join(
        f"{x:.3f} {y:.3f} l\n" for x, y in corners[1:]
    ) + "h\n"
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"q\n" + path.encode("ascii") + b"W n\n"
        + text_op(int(text_x), int(text_y), word, size=10)
        + b"Q\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_153_rotated_clip_corner() -> bytes:
    """A name in the corner a rotated clip's bounding box adds.

    The clip is one rectangle turned 35 degrees. Its box reaches well
    past the rectangle's corners, and the name sits in that margin:
    every pixel of it is clipped away, and the name comes straight out
    of the file. A rotated clip needs no unusual path geometry — a
    landscape scan does it — which is why this matters more than the
    triangle of #150.
    """
    return _rotated_clip(116, 432, "Yamada")


def case_154_rotated_clip_inside() -> bytes:
    """The same rotated clip with the name at its centre.

    The guard: testing the piece as a shape must not start reporting
    what the clip keeps.
    """
    return _rotated_clip(230, 550, "Yamada")


def _inline_image_cover(extra: str, hex_byte: str) -> bytes:
    """A line of text with an inline image laid over it."""
    pdf = Pdf()
    f = helvetica(pdf)
    inline = (
        b"q\n290 0 0 26 68 694 cm\n"
        + f"BI /W 4 /H 4 /CS /G /BPC 8 {extra}/F /AHx ID\n".encode("ascii")
        + (hex_byte * 16).encode("ascii")
        + b">\nEI\nQ\n"
    )
    root, _ = page(pdf, text_op(72, 700, SECRET) + inline, font=f)
    return pdf.build(root)


def case_155_inline_image_decode_inverted() -> bytes:
    """An inline image whose /Decode array makes 255 mean black.

    The bytes say 255 and the page comes out solid black. An
    implementation that reads the bytes without the decode array calls
    it white and reports an opaque cover that is not dark, where the
    text under it is blacked out and fully extractable.

    The library applies /Decode itself and hands the decoded pixels over
    in the operator, so the real bug this found here was larger: an
    inline image has no object id to look up, so its pixels were never
    read at all and EVERY inline cover — including a plain black bar —
    was reported as not dark.
    """
    return _inline_image_cover("/D [1 0] ", "ff")


def case_156_inline_image_white() -> bytes:
    """The same inline image without the decode array, so 255 is white.

    The guard: reading an inline image's pixels must not make every
    inline cover dark. This one hides the text and is not dark, which
    is a look.
    """
    return _inline_image_cover("", "ff")


def case_157_text_field_value_off() -> bytes:
    """A text field whose value is the word "Off".

    /Off is the name a cleared check box holds, and excluding it
    everywhere throws away a value somebody typed because of what a
    button would have meant by it.
    """
    return _acroform_page(
        b"<</Type/Annot/Subtype/Widget/FT/Tx/T(status)/Ff 0"
        b"/Rect[72 700 300 720]/F 4/DA(/Helv 10 Tf 0 g)/V(Off)>>",
        "157",
    )


def case_158_glyph_names_without_tounicode() -> bytes:
    """Text in a font with /Differences glyph names and no /ToUnicode.

    An extractor that maps the glyph names recovers the Greek letters;
    one that gives up maps them to control characters, and a glyph
    nobody could read must still count as covered. A black bar over
    them is a failure either way.
    """
    pdf = Pdf()
    encoding = pdf.add(
        b"<</Type/Encoding/Differences[1/Alpha/Beta/Gamma/Delta/Epsilon]>>"
    )
    font = pdf.add(
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding "
        + str(encoding).encode("ascii")
        + b" 0 R>>"
    )
    body = (
        b"BT\n0 0 0 rg\n/F1 24 Tf\n1 0 0 1 80 700 Tm\n<0102030405> Tj\nET\n"
        + black_rect(76, 694, 120, 34)
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {font} 0 R>>>>/Contents {contents} 0 R>>"
        ).encode("ascii"),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode("ascii"))
    root = pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode("ascii"))
    return pdf.build(root)


def case_159_outline_signature_openaction_xfa() -> bytes:
    """Four things that carry a name and are not on any page.

    A bookmark titled "Draft for Yamada" whose action is a mailto:, a
    signature dictionary holding the signer's name, reason and
    location, a URI the document opens with, and an XFA form packet
    whose data holds what somebody typed.

    None of these is an annotation, so an implementation that reads URI
    actions only from /Annots misses the bookmark's. And the library
    exposes neither the signature details nor a URI open action nor the
    XFA packet, so those have to be read from the bytes — the signer's
    name is a person's name sitting in a file nobody looked at.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(text_op(72, 760, "Public part of the document"))
    outlines = pdf.reserve()
    item = pdf.add(
        (
            f"<</Title(Draft for Yamada)/Parent {outlines} 0 R"
            f"/A<</S/URI/URI(mailto:yamada.taro@example.co.jp)>>>>"
        ).encode("ascii")
    )
    pdf.put(
        outlines,
        f"<</Type/Outlines/First {item} 0 R/Last {item} 0 R/Count 1>>".encode("ascii"),
    )
    signature = pdf.add(
        b"<</Type/Sig/Name(Yamada Taro)/Reason(approved the disclosure)"
        b"/Location(Yokohama head office)/M(D:20260101000000Z)>>"
    )
    field = pdf.add(
        (
            f"<</Type/Annot/Subtype/Widget/FT/Sig/T(signature)"
            f"/Rect[72 600 300 640]/F 4/V {signature} 0 R>>"
        ).encode("ascii")
    )
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
            f"<</Type/Catalog/Pages {pages_num} 0 R/Outlines {outlines} 0 R"
            f"/OpenAction<</S/URI/URI(https://intranet.example.co.jp/?case=yamada-2026)>>"
            f"/AcroForm<</Fields[{field} 0 R]/SigFlags 3"
            f"/XFA[(preamble)(<xdp><datasets><data><applicant>Yamada Taro"
            f"</applicant></data></datasets></xdp>)]>>>>"
        ).encode("ascii")
    )
    return pdf.build(root)


# An L-shaped clip: six vertices, concave, the notch in the top right.
_L_CLIP = [(100, 400), (400, 400), (400, 550), (250, 550), (250, 700), (100, 700)]


def _l_clip(text_x: int, text_y: int) -> bytes:
    path = f"{_L_CLIP[0][0]} {_L_CLIP[0][1]} m\n" + "".join(
        f"{x} {y} l\n" for x, y in _L_CLIP[1:]
    ) + "h\n"
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"q\n" + path.encode("ascii") + b"W n\n"
        + text_op(text_x, text_y, "Yamada", size=10)
        + b"Q\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_160_concave_clip_notch() -> bytes:
    """A name in the notch of an L-shaped clip.

    Six vertices and concave, which is the shape a parity test gets
    wrong when it is subtly broken: the editor team's mutation run found
    a survivor in their polygon crossing test that died only to a
    five-sided polygon, because an L's even edge count can hide a
    negated parity. The name here is inside the L's bounding box, inside
    the notch, and not one pixel of it is drawn.
    """
    return _l_clip(300, 640)


def case_161_concave_clip_lower_arm() -> bytes:
    """The same L-shaped clip with the name in its lower arm."""
    return _l_clip(150, 430)


def case_162_concave_clip_right_arm() -> bytes:
    """The same L-shaped clip with the name in its right arm.

    Both arms, because a parity test that is wrong in one direction can
    be right in the other, and the notch alone would not say which.
    """
    return _l_clip(300, 430)


def case_163_inline_image_filter_chain() -> bytes:
    """An inline image behind two filters, ASCII85 then Flate.

    /F can be a single name or an array, and every filter in the chain
    has to come off before the pixels mean anything. An implementation
    that decodes only the unfiltered and Flate cases reports this one as
    undecodable even after it handles /Decode correctly.
    """
    import base64

    pdf = Pdf()
    f = helvetica(pdf)
    payload = base64.a85encode(zlib.compress(bytes(16))) + b"~>"
    inline = (
        b"q\n290 0 0 26 68 694 cm\n"
        b"BI /W 4 /H 4 /CS /G /BPC 8 /F [/A85 /Fl] ID\n" + payload + b"\nEI\nQ\n"
    )
    root, _ = page(pdf, text_op(72, 700, SECRET) + inline, font=f)
    return pdf.build(root)


def case_164_inline_image_unknown_filter() -> bytes:
    """An inline image behind a filter nothing can decode.

    Both answers are conformant and the manifest accepts either. An
    implementation that refuses to decode reports a cover it could not
    read, which is a review. One that falls back to the raw bytes reads
    whatever they say — here all zeros, so a dark cover and a failure.
    Neither may report a pass, which is the part that matters.

    pdf.js takes the second route and says so only in a console warning,
    and the decoded object is indistinguishable from a properly decoded
    one: same kind, same length. Turning its warnings back on to catch
    that is what once put recovered text in the console, so the browser
    checker lives with the guess and says so here.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    inline = (
        b"q\n290 0 0 26 68 694 cm\n"
        b"BI /W 4 /H 4 /CS /G /BPC 8 /F /VendorCodec ID\n" + bytes(16) + b"\nEI\nQ\n"
    )
    root, _ = page(pdf, text_op(72, 700, SECRET) + inline, font=f)
    return pdf.build(root)


def _clip_top_edge(top: int) -> bytes:
    """A name at baseline 700 with a clip whose top edge is at `top`.

    The glyph box for size 10 runs from y=698 to y=708 (the fixed
    -0.2/0.8 em pair), so `top` places the clip's edge relative to the
    box: 698 touches it exactly, 699 overlaps it by a point, 697 leaves
    a point of daylight.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        f"q\n100 400 300 {top - 400} re\nW n\n".encode("ascii")
        + text_op(120, 700, "Yamada", size=10)
        + b"Q\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_165_clip_edge_tangent() -> bytes:
    """A clip whose top edge sits exactly on the glyph box's bottom.

    Zero area of the glyph is inside the clip and nothing is drawn, and
    the spec's rule is that a glyph is outside only when its box meets
    none of the pieces — a touch is a meeting. So this reads as drawn
    and nothing is reported.

    Pinned deliberately rather than argued with: the whole point of the
    fixed glyph box is that two implementations compute the same answer,
    and a rule that asked whether any INK fell inside the clip would
    need the font metrics the box exists to avoid. Raised with the
    editor team as a spec question.
    """
    return _clip_top_edge(698)


def case_166_clip_edge_into_descent() -> bytes:
    """A clip reaching one point into the glyph box, below the baseline.

    "Yamada" has no descenders, so the point of box the clip catches
    holds no ink: nothing is drawn, and the box model says the glyph is
    inside the clip. The same consequence as #165 by a less exact route,
    and the one that could actually happen — a clip aligned to a table
    row lands inside the line's boxes.
    """
    return _clip_top_edge(699)


def case_167_clip_edge_clear_of_the_box() -> bytes:
    """The same clip a point lower, clear of the glyph box.

    Now the box meets no piece, so the name is reported. The pair with
    #166 marks exactly where the box model draws its line.
    """
    return _clip_top_edge(697)


def _clip_bottom_edge(bottom: int) -> bytes:
    """A name at baseline 700 with a clip whose BOTTOM edge is at `bottom`."""
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        f"q\n100 {bottom} 300 {760 - bottom} re\nW n\n".encode("ascii")
        + text_op(120, 700, "Yamada", size=10)
        + b"Q\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_168_clip_cuts_the_descent_band() -> bytes:
    """A clip whose edge is the baseline, cutting only the descent band.

    Four fifths of each glyph box shows and the word reads perfectly —
    measured, 121 ink pixels. Well above the threshold, so nothing is
    reported. This is the case a visibility threshold must not catch:
    the fixed box carries a descent whether or not the word has
    descenders, so a clip at the baseline always trims a fifth of it.
    """
    return _clip_bottom_edge(700)


def case_169_clip_leaves_an_ascender_sliver() -> bytes:
    """A clip that leaves only the top point of each glyph box.

    A tenth of the box shows and almost nothing of the word — measured,
    4 ink pixels against 121 for #168. Under the threshold, so the name
    is reported: it is extractable and nobody can read it off the page.
    """
    return _clip_bottom_edge(707)


def case_170_overlapping_clip_bands() -> bytes:
    """Two nearly identical thin clip bands over the top of a line.

    Each leaves about a tenth of every glyph box showing and they
    overlap by nine tenths, so the union is an eighth — under the
    visibility threshold — while the plain sum is a fifth, over it. The
    page shows four ink pixels, the same as #169, and the name is
    extractable.

    The editor team asked whether such a file existed, since their
    engine unions the pieces and mine summed them. It does, and this is
    it: summing double-counts the overlap, over-states what shows, and
    under-reports.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"q\n100 706.9 300 1.1 re\n100 707.0 300 1.1 re\nW n\n"
        + text_op(120, 700, "Yamada", size=10)
        + b"Q\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_171_clip_with_too_many_pieces() -> bytes:
    """Thirteen overlapping clip bands crowding one line.

    More pieces over a single glyph than the union computation is
    bounded at. The summed area was the old answer and it is not an
    answer — it can only over-state what shows, so it can only hide
    text. The bound now reports that it could not evaluate the clip and
    the page goes to "needs a look", which costs a reader almost
    nothing on a file this strange and rules out the quiet miss.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    bands = "".join(
        f"100 {706.0 + i * 0.15:.2f} 300 1.1 re\n" for i in range(13)
    )
    body = (
        b"q\n" + bands.encode("ascii") + b"W n\n"
        + text_op(120, 700, "Yamada", size=10)
        + b"Q\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)



def _scan_with_band(pdf: Pdf) -> int:
    """A mostly-white page scan with one black band burnt into the
    pixels — what a scanner produces when somebody blacks a line out
    with a marker before scanning, and what a redaction tool produces
    when it rewrites the pixels it covers.

    The band is one row of eight, so the image as a whole is nowhere
    near dark: measuring darkness over the whole image is what makes
    the band invisible to the checker."""
    rows: list[int] = []
    for y in range(8):
        for x in range(8):
            rows += [0, 0, 0] if y == 3 else [245, 244, 240]
    return pdf.stream(
        bytes(rows),
        "/Type/XObject/Subtype/Image/Width 8/Height 8"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )


def case_172_scan_with_a_burnt_in_band() -> bytes:
    """A scan whose blackout is part of the image, with the OCR text
    still under it. The band is an eighth of the image, so an
    implementation that asks "is this image dark?" instead of "is this
    image dark HERE" sees a 12%-dark image, decides it is not a cover,
    and reports a document whose blacked-out line is fully recoverable
    as clean. Band row 3 of 8 lands at y 421-501 on the page; the
    hidden line sits at y 460."""
    pdf = Pdf()
    f = helvetica(pdf)
    img = _scan_with_band(pdf)
    body = (
        b"q\n451 0 0 638 72 102 cm\n/Im0 Do\nQ\n"
        # Under the band: invisible on screen, recoverable from the file.
        + text_op(90, 460, "Account 3312-8890 held by the former chair", tr=3)
        # Away from the band: ordinary OCR text, not a finding.
        + text_op(90, 700, "Minutes of the meeting held on 2026-04-01", tr=3)
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_173_rotated_cover_over_blacked_image() -> bytes:
    """A correct redaction under a cover that is not a box: the image's
    own pixels are black everywhere the cover sits, and the cover is a
    square turned 30 degrees. It must come back clean.

    An implementation that samples the cover's bounding box instead of
    its shape sees the corners the box adds — image nobody blacked out
    and nobody is hiding anything with — and reports a correct
    redaction as a leak. The most expensive mistake this tool can make,
    so this fixture is a pass, not a fail.

    The blacked region is the cover grown by three image pixels, so
    that quantising page coordinates to pixel centres along the rotated
    edge cannot leave a pale pixel under the cover itself."""
    pdf = Pdf()
    f = helvetica(pdf)
    n = 48
    margin = 3 * (400 / n)
    ang = math.radians(30)
    cos_a, sin_a = math.cos(ang), math.sin(ang)

    def under_cover(px: float, py: float) -> bool:
        dx, dy = px - 200, py - 400
        bx = dx * cos_a + dy * sin_a
        by = -dx * sin_a + dy * cos_a
        return -margin <= bx <= 200 + margin and -margin <= by <= 200 + margin

    pixels = bytearray()
    for row in range(n):
        for col in range(n):
            px = 100 + ((col + 0.5) / n) * 400
            py = 300 + (1 - (row + 0.5) / n) * 400
            pixels += (
                b"\x00\x00\x00" if under_cover(px, py) else bytes((200, 200, 200))
            )
    img = pdf.stream(
        bytes(pixels),
        f"/Type/XObject/Subtype/Image/Width {n}/Height {n}"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    body = (
        text_op(72, 760, "Site photograph, one face removed")
        + b"q\n400 0 0 400 100 300 cm\n/Im0 Do\nQ\n"
        + f"q\n{cos_a:.5f} {sin_a:.5f} {-sin_a:.5f} {cos_a:.5f} 200 400 cm\n".encode()
        + b"0 0 0 rg\n0 0 200 200 re\nf\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)



def case_174_black_image_over_photo() -> bytes:
    """#07's leak with the cover drawn as an image instead of a
    rectangle: a black block stretched over a face, the photo's own
    pixels untouched. Whoever opens the file in anything that ignores
    the block gets the face back. A6 asks what the candidate hides, not
    how the candidate was drawn, and restricting it to filled paths let
    this through while the identical rectangle failed."""
    pdf = Pdf()
    f = helvetica(pdf)
    pic = photo(pdf)
    blk = black_block(pdf)
    body = (
        text_op(72, 780, "Photo with the face removed")
        + b"q\n300 0 0 120 72 620 cm\n/Im0 Do\nQ\n"
        + b"q\n90 0 0 70 140 650 cm\n/Im1 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": pic, "Im1": blk})
    return pdf.build(root)


def case_175_marker_stroke_over_photo() -> bytes:
    """The same leak drawn with a thick stroke — the marker-pen
    blackout. 40pt of black line across a face, photo pixels
    untouched."""
    pdf = Pdf()
    f = helvetica(pdf)
    pic = photo(pdf)
    body = (
        text_op(72, 780, "Photo with the face removed")
        + b"q\n300 0 0 120 72 620 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n40 w\n150 680 m\n260 680 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": pic})
    return pdf.build(root)


def case_176_marker_over_blacked_image() -> bytes:
    """The negative test for the two above, and for the shape of a
    stroked cover: the image pixels under a diagonal marker stroke
    really are black, so the file is clean and must come back clean.

    A stroked segment's bounding box is far bigger than the ribbon it
    paints — here about a fifth of the box is ribbon — so an
    implementation that samples the box sees untouched image in the
    corners (renderer: 0% dark at 400,360-440,400) and reports a
    correct redaction as a leak."""
    pdf = Pdf()
    f = helvetica(pdf)
    n = 48
    half = 30.0
    margin = 3 * (400 / n)
    ax, ay, bx, by = 150.0, 350.0, 450.0, 650.0
    length = math.hypot(bx - ax, by - ay)
    ux, uy = (bx - ax) / length, (by - ay) / length

    def under_marker(px: float, py: float) -> bool:
        rx, ry = px - ax, py - ay
        along = rx * ux + ry * uy
        across = -rx * uy + ry * ux
        return (
            -(half + margin) <= along <= length + half + margin
            and abs(across) <= half + margin
        )

    pixels = bytearray()
    for row in range(n):
        for col in range(n):
            px = 100 + ((col + 0.5) / n) * 400
            py = 300 + (1 - (row + 0.5) / n) * 400
            pixels += (
                b"\x00\x00\x00" if under_marker(px, py) else bytes((200, 200, 200))
            )
    img = pdf.stream(
        bytes(pixels),
        f"/Type/XObject/Subtype/Image/Width {n}/Height {n}"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    body = (
        text_op(72, 760, "Site photograph, one face removed")
        + b"q\n400 0 0 400 100 300 cm\n/Im0 Do\nQ\n"
        # 2 J: projecting square caps, which is the ribbon the spec
        # describes - the segment grown by half the pen width all round.
        + b"q\n0 0 0 RG\n60 w\n2 J\n150 350 m\n450 650 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)



def case_177_inline_separation_type4() -> bytes:
    """An inline image in a Separation colour space whose tint
    transform is a PostScript calculator function (FunctionType 4).

    The bytes are 0xff — full tint — and the function maps tint t to
    (1-t, 1-t, 1-t), so the image paints solid black over the text. Get
    the colour space wrong and the same bytes read as white: a cover
    that is not dark, and a document whose covered line is still
    extractable reported as clean.

    An inline image cannot hold an indirect reference, so the colour
    space is a named page resource and the function is only reachable
    through it. An implementation that cannot evaluate a Type 4
    function must report the cover as unread (review), never a pass.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    fn = pdf.stream(
        b"{ 1 exch sub dup dup }",
        "/FunctionType 4/Domain[0 1]/Range[0 1 0 1 0 1]",
    )
    cs = pdf.add(f"[/Separation/Black/DeviceRGB {fn} 0 R]".encode())
    inline = (
        b"q\n290 0 0 26 68 694 cm\n"
        b"BI /W 4 /H 4 /CS /CS0 /BPC 8 ID\n" + b"\xff" * 16 + b"\nEI\nQ\n"
    )
    root, _ = page(
        pdf, text_op(72, 700, SECRET) + inline, font=f, colorspaces={"CS0": cs}
    )
    return pdf.build(root)



def _band_scan(pdf: Pdf, rows: tuple[int, ...], cols: tuple[int, ...] | None = None) -> int:
    """A pale 16x16 page scan with one blacked-out block burnt in.

    `rows` and `cols` are the block's own cells; the rest is paper. The
    page places it at 72,102 size 451x638, so cell (row, col) covers
    x 72+28.19*col .. +28.19 and y 740-39.875*(row+1) .. +39.875.
    """
    out: list[int] = []
    for y in range(16):
        for x in range(16):
            burnt = y in rows and (cols is None or x in cols)
            out += [0, 0, 0] if burnt else [245, 244, 240]
    return pdf.stream(
        bytes(out),
        "/Type/XObject/Subtype/Image/Width 16/Height 16"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )


def case_178_band_over_part_of_a_run() -> bytes:
    """A burnt-in band that covers eight characters in the middle of a
    66-character invisible OCR line.

    Measuring the image's darkness over the run's own rectangle leaves
    it an eighth dark — below any threshold, so the band stops being a
    cover and the account number under it is reported as nothing at
    all. Darkness has to be asked per glyph, which is the unit every
    other coverage rule here already uses. Cells (6,7) x (4,5) put the
    band at x 184.8-241.1, y 421-500.8; the line sits at y 460.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    img = _band_scan(pdf, (6, 7), (4, 5))
    body = b"q\n451 0 0 638 72 102 cm\n/Im0 Do\nQ\n" + text_op(
        90,
        460,
        "Reference number 3312-8890 was issued to the former chair in error",
        tr=3,
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_179_band_under_a_doubled_run() -> bytes:
    """The same invisible line shown twice at the same place, over a
    band burnt into the scan.

    Each run finds the same dark band, so the band is discovered twice,
    and two identical regions each contain the other. Dropping "the one
    inside the other" dropped both: the band stopped being a cover at
    all and a line recoverable twice over came back clean. One finding
    is the right answer, not two and not none.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    # Rows 6 and 7 of 16 put the band at y 421-500.8, over the line.
    img = _band_scan(pdf, (6, 7))
    line = "Account 3312-8890 held by the former chair"
    body = (
        b"q\n451 0 0 638 72 102 cm\n/Im0 Do\nQ\n"
        + text_op(90, 460, line, tr=3)
        + text_op(90, 460, line, tr=3)
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_180_anisotropic_stroke_over_photo() -> bytes:
    """A 10pt pen under `10 0 0 1 0 0`: the stroke a reader sees is
    100pt wide, not 32.

    One number for the pen width — sqrt(|det CTM|) — is only right when
    the scaling is uniform. Here it models a 32pt band down the middle
    of a 100pt one, the image is black exactly there and the original
    picture is still in the file either side of it, and the check looks
    at the sliver and calls the redaction done.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    pixels = bytearray()
    for row in range(48):
        for col in range(40):
            blacked = 17 <= col <= 23 and 4 <= row <= 44
            pixels += b"\x00\x00\x00" if blacked else bytes((200, 200, 200))
    img = pdf.stream(
        bytes(pixels),
        "/Type/XObject/Subtype/Image/Width 40/Height 48"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    body = (
        text_op(72, 760, "Elevation, one window blacked out")
        + b"q\n200 0 0 240 100 380 cm\n/Im0 Do\nQ\n"
        + b"q\n10 0 0 1 0 0 cm\n0 0 0 RG\n10 w\n20 400 m\n20 600 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_181_miter_join_over_photo() -> bytes:
    """A wide marker stroke drawn as a V, with the image blacked out
    under the two straight runs and left intact under the corner.

    A join paints a wedge outside both of them — a miter join, the PDF
    default, reaches up to `miterLimit` times the half width past the
    corner. Model the stroke as two independent ribbons and the wedge
    is covered on screen and never looked at, so the picture inside it
    stays recoverable and the file reads as clean.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    half, margin = 30.0, 6.0
    corners = ((150.0, 400.0), (300.0, 600.0), (450.0, 400.0))

    def under_ribbon(
        p: tuple[float, float], q: tuple[float, float], x: float, y: float
    ) -> bool:
        dx, dy = q[0] - p[0], q[1] - p[1]
        length = math.hypot(dx, dy)
        ux, uy = dx / length, dy / length
        rx, ry = x - p[0], y - p[1]
        along = rx * ux + ry * uy
        across = -rx * uy + ry * ux
        return 0 <= along <= length and abs(across) <= half + margin

    pixels = bytearray()
    for row in range(70):
        for col in range(80):
            x = 100 + ((col + 0.5) / 80) * 400
            y = 350 + (1 - (row + 0.5) / 70) * 350
            blacked = under_ribbon(corners[0], corners[1], x, y) or under_ribbon(
                corners[1], corners[2], x, y
            )
            pixels += b"\x00\x00\x00" if blacked else bytes((200, 200, 200))
    img = pdf.stream(
        bytes(pixels),
        "/Type/XObject/Subtype/Image/Width 80/Height 70"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    body = (
        text_op(72, 760, "Roof detail, one skylight blacked out")
        + b"q\n400 0 0 350 100 350 cm\n/Im0 Do\nQ\n"
        # Default joins and caps: miter, butt.
        + b"q\n0 0 0 RG\n60 w\n150 400 m\n300 600 l\n450 400 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_182_butt_cap_stroke_correct() -> bytes:
    """A correct redaction under a short, wide, butt-capped stroke: 10pt
    of travel, 40pt across, with the image blacked out exactly where it
    paints. It must come back clean.

    Butt caps are the PDF default and they stop at the endpoint.
    Modelling every cap as a projecting square adds 20pt at each end —
    four fifths of the modelled cover is then image nobody covered and
    nobody is hiding anything with, and a correct redaction reads as a
    leak.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    pixels = bytearray()
    for row in range(20):
        for col in range(40):
            x = 100 + ((col + 0.5) / 40) * 200
            y = 450 + (1 - (row + 0.5) / 20) * 100
            blacked = 195 <= x <= 215 and 475 <= y <= 525
            pixels += b"\x00\x00\x00" if blacked else bytes((200, 200, 200))
    img = pdf.stream(
        bytes(pixels),
        "/Type/XObject/Subtype/Image/Width 40/Height 20"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    body = (
        text_op(72, 760, "Detail, one label blacked out")
        + b"q\n200 0 0 100 100 450 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n40 w\n200 500 m\n210 500 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_183_rotated_image_cover_over_strip() -> bytes:
    """A black image turned 45 degrees over a night photograph that was
    blacked out everywhere except one strip inside the cover.

    The cover's bounding box is twice the cover, and the extra is
    already black, so measuring the box reports 96% dark and the strip
    — 7.5% of what the cover actually hides — is outvoted. An image's
    cover region is the parallelogram its matrix maps the unit square
    to, not the box around it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    pixels = bytearray()
    for row in range(80):
        for col in range(80):
            x = 100 + ((col + 0.5) / 80) * 400
            y = 300 + (1 - (row + 0.5) / 80) * 400
            strip = 225 <= x <= 375 and 490 <= y <= 510
            pixels += bytes((200, 200, 200)) if strip else b"\x00\x00\x00"
    photo_obj = pdf.stream(
        bytes(pixels),
        "/Type/XObject/Subtype/Image/Width 80/Height 80"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    cover = pdf.stream(
        b"\x00" * 48,
        "/Type/XObject/Subtype/Image/Width 4/Height 4"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    side = 200 * math.cos(math.radians(45))
    body = (
        text_op(72, 760, "Night shot, one sign blacked out")
        + b"q\n400 0 0 400 100 300 cm\n/Im0 Do\nQ\n"
        + f"q\n{side:.4f} {side:.4f} {-side:.4f} {side:.4f} 300 358.5786 cm\n".encode()
        + b"/Im1 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": photo_obj, "Im1": cover})
    return pdf.build(root)


def case_184_clipped_cover_over_photo() -> bytes:
    """A black rectangle clipped to a thin L, over a plan that was
    blacked out everywhere except along the middle of each arm.

    The cover's rectangle is trimmed to the clip's bounding BOX, which
    is right for its extent and wrong for what it hides: the L is a
    fiftieth of that box, the rest of the box is already black, and the
    intact corridor the cover really sits on is outvoted fifty to one.
    A sample counts only where the clip actually lets the cover paint.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    pixels = bytearray()
    for row in range(125):
        for col in range(125):
            x = 150 + ((col + 0.5) / 125) * 500
            y = 150 + (1 - (row + 0.5) / 125) * 500
            intact = (202 <= x <= 206 and 200 <= y <= 600) or (
                200 <= x <= 600 and 202 <= y <= 206
            )
            pixels += bytes((200, 200, 200)) if intact else b"\x00\x00\x00"
    img = pdf.stream(
        bytes(pixels),
        "/Type/XObject/Subtype/Image/Width 125/Height 125"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    body = (
        text_op(72, 760, "Plan, one corridor blacked out")
        + b"q\n500 0 0 500 150 150 cm\n/Im0 Do\nQ\n"
        + b"q\n200 200 8 400 re\n200 200 400 8 re\nW n\n"
        + b"0 0 0 rg\n150 150 500 500 re\nf\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_185_comb_image_under_cover() -> bytes:
    """A striped image under a cover that spans it exactly: every third
    column black, the two between them the original picture.

    192 columns across a region sampled 64 times lands every sample on
    the centre of a black column, so a fixed grid of sample points
    reports the region fully dark and two thirds of the covered picture
    stays in the file. A grid is a comb, and a comb lines up with a
    pattern of its own period; the sample offsets have to be uneven,
    and fixed, so the reading is both unaliased and repeatable.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    pixels = bytearray()
    for _ in range(16):
        for col in range(192):
            pixels += b"\x00\x00\x00" if col % 3 == 1 else bytes((200, 200, 200))
    img = pdf.stream(
        bytes(pixels),
        "/Type/XObject/Subtype/Image/Width 192/Height 16"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    body = (
        text_op(72, 760, "Barcode strip, one third blacked out")
        + b"q\n384 0 0 80 100 400 cm\n/Im0 Do\nQ\n"
        + black_rect(100, 400, 384, 80)
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)



def _scan_with_heading_band(pdf: Pdf, rows: tuple[int, int]) -> int:
    """A pale scan with a black band across it, placed by the caller at
    72,400 size 451x80 so that one image row is one point of the page."""
    out: list[int] = []
    for y in range(80):
        for _ in range(64):
            out += [0, 0, 0] if rows[0] <= y <= rows[1] else [245, 244, 240]
    return pdf.stream(
        bytes(out),
        "/Type/XObject/Subtype/Image/Width 64/Height 80"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )


def case_186_band_just_short_of_the_glyph_box() -> bytes:
    """A band burnt into a scan that covers 93% of each glyph box of an
    invisible OCR line — enough that a reader sees nothing of it.

    One number for the whole box cannot answer the coverage rules: 93%
    is short of the 95% a cover candidate needs, so the band was no
    cover and the line went unreported. The box has to be divided into
    cells, each cell judged by the candidate rule and the box by the
    coverage rule.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    # Rows 9-21 of 80 land at y 458-471; the glyph boxes are 457.2-471.2.
    img = _scan_with_heading_band(pdf, (9, 21))
    body = b"q\n451 0 0 80 72 400 cm\n/Im0 Do\nQ\n" + text_op(
        90, 460, "Account 3312-8890 held by the former chair", tr=3
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_187_reversed_heading() -> bytes:
    """White letters reversed out of a scanned black heading. A reader
    reads them easily, and nothing is wrong with the file.

    The guard for A4: an image declares no colour, and skipping it put
    paper white behind every run on a scan, so white text on a black
    band read as white on white and a perfectly ordinary heading was
    reported as hidden text.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    img = _scan_with_heading_band(pdf, (4, 24))
    body = b"q\n451 0 0 80 72 400 cm\n/Im0 Do\nQ\n" + text_op(
        90, 460, "SECTION 4 - CONFIDENTIAL ANNEX", rgb="1 1 1"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_188_black_on_scanned_band() -> bytes:
    """Black letters sitting on the black band of a scan: nothing of
    them appears on the page, and every word is still in the file.

    The other half of #187. With the image's pixels left unasked the
    background is paper white, so black text on a black band reads as
    high contrast — the most ordinary way to hide a line in a scanned
    document, reported as nothing at all.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    img = _scan_with_heading_band(pdf, (4, 24))
    body = b"q\n451 0 0 80 72 400 cm\n/Im0 Do\nQ\n" + text_op(
        90, 460, "Account 3312-8890 held by the former chair"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)



def _render_mode_page(prefix: bytes, body_text: str = SECRET) -> bytes:
    """One line of 28pt text with the colour and rendering mode the
    caller sets, on an otherwise blank page."""
    pdf = Pdf()
    f = helvetica(pdf)
    content = (
        b"BT\n" + prefix + b"/F1 28 Tf\n1 0 0 1 72 700 Tm\n("
        + body_text.encode()
        + b") Tj\nET\n"
    )
    root, _ = page(pdf, content, font=f)
    return pdf.build(root)


def case_189_outlined_title() -> bytes:
    """A title in white letters outlined in black: rendering mode 2
    fills with one colour and strokes with another. A reader reads it
    without difficulty and nothing is wrong with the file.

    Judging the glyphs by their fill alone makes them white on white
    and reports an ordinary title as hidden text. The colours a glyph
    paints with depend on its mode — the fill for 0, 2, 4 and 6, the
    stroke for 1, 2, 5 and 6 — and text is unreadable only when all of
    them sit on the background.
    """
    return _render_mode_page(b"1 1 1 rg\n0 0 0 RG\n0.7 w\n2 Tr\n", "OUTLINED TITLE")


def case_190_stroke_only_white_text() -> bytes:
    """Rendering mode 1 outlines and does not fill, with a white stroke
    on white paper: nothing of the line appears, and every word is
    still in the file.

    The mirror of #189. Read the fill colour — black here, and never
    painted — and the line reads as high contrast, so the most direct
    way to leave a line invisible in mode 1 was reported as nothing.
    """
    return _render_mode_page(b"0 0 0 rg\n1 1 1 RG\n0.7 w\n1 Tr\n")


def case_191_tr3_under_unreadable_cover() -> bytes:
    """Invisible (Tr 3) text under an image whose pixels no decoder
    here can read.

    Not a failure: the image may be a photograph rather than a
    blackout. But not silence either — the text is recoverable and
    whether anything hides it is exactly what could not be established,
    so the file earns a look and a note saying why.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    img = pdf.stream(
        b"\x00" * 64,
        "/Type/XObject/Subtype/Image/Width 8/Height 8"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8/Filter/JPXDecode",
    )
    body = b"q\n290 0 0 26 68 694 cm\n/Im0 Do\nQ\n" + text_op(72, 700, SECRET, tr=3)
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_192_form_inside_text_object() -> bytes:
    """A Form XObject drawn inside the parent's text object, with a
    text matrix of its own, before the parent shows its line.

    The parent's line has to land where the parent's own Tm put it —
    under the bar — not where the form left the text matrix. Lose that
    and the covered line is measured somewhere else on the page and the
    cover reads as covering nothing.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    form = pdf.stream(
        b"BT\n/F1 14 Tf\n1 0 0 1 400 120 Tm\n(inner) Tj\nET\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 612 792]"
        f"/Resources<</Font<</F1 {f} 0 R>>>>",
    )
    body = (
        b"BT\n/F1 14 Tf\n1 0 0 1 72 700 Tm\n/Fm0 Do\n("
        + SECRET.encode()
        + b") Tj\nET\n"
        + black_rect(68, 694, 290, 26)
    )
    root, _ = page(pdf, body, font=f, xobjects={"Fm0": form})
    return pdf.build(root)



def case_193_tagged_strings_past_the_cap() -> bytes:
    """Sixty /ActualText entries: fifty captions that are also on the
    page, then ten names that are not.

    An implementation that reads only the first fifty finds nothing
    wrong and must say so — the file earns a look and a note that the
    list was cut short, never a pass. One that reads all of them finds
    the ten names and fails the file; both answers are conformant, and
    reporting nothing at all is not.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    visible = "".join(f"Figure {i} caption " for i in range(50))
    tagged = "".join(
        f"/Span{i}<</ActualText(Figure {i} caption)>>" for i in range(50)
    )
    tagged += "".join(
        f"/Hidden{i}<</ActualText(Removed name {i} Yamada)>>" for i in range(10)
    )
    body = b""
    words = visible.split()
    per_row = 15
    for row in range((len(words) + per_row - 1) // per_row):
        line = " ".join(words[row * per_row : (row + 1) * per_row])
        body += text_op(72, 700 - row * 16, line, size=8)
    root, _ = page(pdf, body, font=f, extra_page=f"/PieceInfo<<{tagged}>>")
    return pdf.build(root)



def case_194_earlier_revision_many_strings() -> bytes:
    """An earlier revision holding sixty lines of a draft, read by an
    implementation that stops at fifty.

    What it read is a failure in its own right, and the ten lines it
    did not read are the ones it must not pass over in silence: the
    skip list says how far the reading got. A cap may never read as
    "that was all of it".
    """
    pdf = Pdf()
    f = helvetica(pdf)
    root, _ = page(pdf, text_op(72, 700, "Final version"), font=f)
    final = pdf.build(root)

    draft_pdf = Pdf()
    draft_font = helvetica(draft_pdf)
    body = b"".join(
        text_op(72, 700 - i * 10, f"Line {i} of the draft", size=8)
        for i in range(60)
    )
    draft_root, _ = page(draft_pdf, body, font=draft_font)
    return draft_pdf.build(draft_root) + b"\n" + final



def case_195_pattern_filled_text() -> bytes:
    """A line painted with a tiling pattern instead of a colour.

    What colour it is cannot be read from the content stream, so
    whether it can be read where it sits is exactly what cannot be
    established. Saying nothing claimed it could.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    cell = pdf.stream(
        b"0.5 0.5 0.5 rg\n0 0 4 4 re\nf\n",
        "/Type/Pattern/PatternType 1/PaintType 1/TilingType 1"
        "/BBox[0 0 8 8]/XStep 8/YStep 8/Resources<<>>",
    )
    body = (
        b"BT\n/Pattern cs\n/P0 scn\n/F1 28 Tf\n1 0 0 1 72 700 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/Pattern<</P0 {cell} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))



def _point_in_polygon(poly: list[tuple[float, float]], x: float, y: float) -> bool:
    inside = False
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            inside = not inside
    return inside


def _in_ribbon(
    p: tuple[float, float],
    q: tuple[float, float],
    x: float,
    y: float,
    half: float,
) -> bool:
    """Is (x, y) within `half` of the segment p-q, measured across it,
    and between its ends? The footprint of a butt-capped stroke."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    rx, ry = x - p[0], y - p[1]
    return 0 <= rx * ux + ry * uy <= length and abs(-rx * uy + ry * ux) <= half


def _image_from_test(
    pdf: Pdf,
    cells: int,
    x0: float,
    y0: float,
    w: float,
    h: float,
    covered,
) -> int:
    """A grey picture blacked out wherever `covered(x, y)` says so, laid
    out for a placement of w x h at (x0, y0)."""
    pixels = bytearray()
    for row in range(cells):
        for col in range(cells):
            x = x0 + ((col + 0.5) / cells) * w
            y = y0 + (1 - (row + 0.5) / cells) * h
            pixels += b"\x00\x00\x00" if covered(x, y) else bytes((200, 200, 200))
    return pdf.stream(
        bytes(pixels),
        f"/Type/XObject/Subtype/Image/Width {cells}/Height {cells}"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )


def case_196_curved_marker_correct() -> bytes:
    """A correct redaction under a 40pt curved marker stroke.

    A Bézier's control points are not points on the curve: the control
    polygon of `150 400 m 150 600 450 600 450 400 c` bulges 50pt above
    the curve's own apex. Follow it and the stroke is modelled over
    picture nobody covered — a correctly prepared document read as a
    leak, five times over. The curve has to be flattened into short
    segments first.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    start, c1, c2, end = (150.0, 400.0), (150.0, 600.0), (450.0, 600.0), (450.0, 400.0)

    def on_curve(t: float) -> tuple[float, float]:
        u = 1 - t
        return (
            u * u * u * start[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * end[0],
            u * u * u * start[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * end[1],
        )

    along = [on_curve(i / 200) for i in range(201)]

    def covered(x: float, y: float) -> bool:
        return any((x - px) ** 2 + (y - py) ** 2 <= 28.0**2 for px, py in along)

    img = _image_from_test(pdf, 96, 100.0, 350.0, 400.0, 400.0, covered)
    body = (
        text_op(72, 760, "Route sketch, one stretch blacked out")
        + b"q\n400 0 0 400 100 350 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n40 w\n1 J\n1 j\n150 400 m 150 600 450 600 450 400 c\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_197_miter_join_repeated_vertex() -> bytes:
    """#181's leak with the corner vertex written twice.

    `A m B l B l C l S` draws the same mitred V, and the zero-length
    segment in the middle has no direction — so the corner it sits on
    lost its join, the wedge went unmodelled, and the picture inside it
    stayed recoverable with the file reported clean. Repeated points
    have to come out before the corners are worked out.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    corners = ((150.0, 400.0), (300.0, 600.0), (450.0, 400.0))

    def covered(x: float, y: float) -> bool:
        return _in_ribbon(corners[0], corners[1], x, y, 36.0) or _in_ribbon(
            corners[1], corners[2], x, y, 36.0
        )

    img = _image_from_test(pdf, 80, 100.0, 350.0, 400.0, 350.0, covered)
    body = (
        text_op(72, 760, "Roof detail, one skylight blacked out")
        + b"q\n400 0 0 350 100 350 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n60 w\n150 400 m\n300 600 l\n300 600 l\n450 400 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_198_round_cap_stroke_correct() -> bytes:
    """A correct redaction under a 100pt round-capped stroke with 10pt
    of travel, so the caps are most of what it paints.

    A round cap is a half-disc; a projecting one is a square that
    reaches the same distance but fills the corners too. Model the
    first as the second and four corners of untouched picture join the
    cover, and a correctly prepared document reads as a leak.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    a, b, half = (295.0, 500.0), (305.0, 500.0), 50.0

    def covered(x: float, y: float) -> bool:
        if x < a[0]:
            distance = math.hypot(x - a[0], y - a[1])
        elif x > b[0]:
            distance = math.hypot(x - b[0], y - b[1])
        else:
            distance = abs(y - a[1])
        return distance <= half + 3

    img = _image_from_test(pdf, 80, 200.0, 420.0, 200.0, 160.0, covered)
    body = (
        text_op(72, 760, "Detail, one face blacked out")
        + b"q\n200 0 0 160 200 420 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n100 w\n1 J\n295 500 m 305 500 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_199_bevel_join_correct() -> bytes:
    """A correct redaction under a bevel-joined V (`2 j`).

    A bevel cuts the corner off with a straight edge across the two
    outer edges; a disc at the corner reaches further in every
    direction. Model the first as the second — or as anything round —
    and the picture outside the bevel joins the cover, failing a
    correctly prepared document.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    a, v, b, half = (150.0, 400.0), (300.0, 600.0), (450.0, 400.0), 34.0
    u1 = ((v[0] - a[0]) / math.hypot(v[0] - a[0], v[1] - a[1]),
          (v[1] - a[1]) / math.hypot(v[0] - a[0], v[1] - a[1]))
    u2 = ((b[0] - v[0]) / math.hypot(b[0] - v[0], b[1] - v[1]),
          (b[1] - v[1]) / math.hypot(b[0] - v[0], b[1] - v[1]))
    sign = -1 if u1[0] * u2[1] - u1[1] * u2[0] > 0 else 1
    n1 = (-u1[1] * sign * half, u1[0] * sign * half)
    n2 = (-u2[1] * sign * half, u2[0] * sign * half)
    wedge = [v, (v[0] + n1[0], v[1] + n1[1]), (v[0] + n2[0], v[1] + n2[1])]

    def covered(x: float, y: float) -> bool:
        return (
            _in_ribbon(a, v, x, y, half)
            or _in_ribbon(v, b, x, y, half)
            or _point_in_polygon(wedge, x, y)
        )

    img = _image_from_test(pdf, 80, 100.0, 350.0, 400.0, 350.0, covered)
    body = (
        text_op(72, 760, "Roof detail, one skylight blacked out")
        + b"q\n400 0 0 350 100 350 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n60 w\n2 j\n150 400 m\n300 600 l\n450 400 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_200_closed_two_point_stroke() -> bytes:
    """`200 500 m 300 500 l h S` with a 40pt pen: a viewer paints a
    plain 100x40 rectangle, because a reversal has no miter and a bevel
    across it has no width.

    Treat the two 180-degree corners as round and the cover grows half
    the pen's width past each end, over picture nobody covered.
    """
    pdf = Pdf()
    f = helvetica(pdf)

    def covered(x: float, y: float) -> bool:
        return 198 <= x <= 302 and 478 <= y <= 522

    img = _image_from_test(pdf, 60, 150.0, 450.0, 300.0, 100.0, covered)
    body = (
        text_op(72, 760, "Detail, one label blacked out")
        + b"q\n300 0 0 100 150 450 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n40 w\n200 500 m 300 500 l h S\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_201_dashed_rule_over_image() -> bytes:
    """A dashed boundary line over a picture blacked out only under the
    dashes.

    The gaps show the picture, and nothing is hidden in them. Ignore
    the dash pattern and the line is one continuous cover across
    untouched picture, which fails a document with nothing wrong with
    it.
    """
    pdf = Pdf()
    f = helvetica(pdf)

    def covered(x: float, y: float) -> bool:
        painted = int((x - 100) // 20) % 2 == 0
        return painted and 490 <= y <= 510

    pixels = bytearray()
    for row in range(24):
        for col in range(120):
            x = 100 + ((col + 0.5) / 120) * 480
            y = 480 + (1 - (row + 0.5) / 24) * 40
            pixels += b"\x00\x00\x00" if covered(x, y) else bytes((200, 200, 200))
    img = pdf.stream(
        bytes(pixels),
        "/Type/XObject/Subtype/Image/Width 120/Height 24"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    body = (
        text_op(72, 760, "Boundary line, drawn dashed")
        + b"q\n480 0 0 40 100 480 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n20 w\n[20 20] 0 d\n100 500 m 580 500 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_202_anisotropic_diagonal_correct() -> bytes:
    """A 10pt pen on a short diagonal under `10 0 0 1 0 0`, with the
    picture blacked out exactly where the ink lands.

    The pen is round in USER space, so under that matrix the ink is a
    parallelogram — not a rectangle square to the device-space
    direction, and not the same width either. Work the region out from
    a width across and a width along, as a device-space rectangle, and
    it covers picture the stroke never touched: a correct redaction
    read as a leak.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    a, b, half = (20.0, 400.0), (22.0, 420.0), 5.0
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    nx, ny = -uy * half, ux * half
    user = [
        (a[0] + nx, a[1] + ny),
        (b[0] + nx, b[1] + ny),
        (b[0] - nx, b[1] - ny),
        (a[0] - nx, a[1] - ny),
    ]
    device = [(10 * x, y) for x, y in user]
    cx = sum(p[0] for p in device) / 4
    cy = sum(p[1] for p in device) / 4
    grown = []
    for x, y in device:
        distance = math.hypot(x - cx, y - cy) or 1
        grown.append((x + (x - cx) / distance * 6, y + (y - cy) / distance * 6))

    img = _image_from_test(
        pdf, 96, 150.0, 370.0, 150.0, 90.0,
        lambda x, y: _point_in_polygon(grown, x, y),
    )
    body = (
        text_op(72, 760, "Elevation, one window blacked out")
        + b"q\n150 0 0 90 150 370 cm\n/Im0 Do\nQ\n"
        + b"q\n10 0 0 1 0 0 cm\n0 0 0 RG\n10 w\n20 400 m\n22 420 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_203_image_clipped_to_an_L() -> bytes:
    """A photograph, then a mostly-pale image clipped to a thin black L
    laid over it. A reader sees a black L across the photograph.

    Deciding whether an image is a cover by measuring the whole of it
    sees the pale pixels outside the clip, calls it "not dark", and
    never asks what it covers — so the photograph under the L, which is
    the original and fully recoverable, is reported as nothing. An
    image's darkness has to be measured where it actually paints.
    """
    pdf = Pdf()
    f = helvetica(pdf)

    def in_l(x: float, y: float) -> bool:
        return (200 <= x <= 230 and 200 <= y <= 500) or (
            200 <= x <= 500 and 200 <= y <= 230
        )

    cover = _image_from_test(pdf, 100, 150.0, 150.0, 500.0, 500.0, in_l)
    body = (
        text_op(72, 760, "Plan with one corridor blacked out")
        + b"q\n300 0 0 300 200 200 cm\n/Im0 Do\nQ\n"
        + b"q\n200 200 30 300 re\n200 200 300 30 re\nW n\n"
        + b"500 0 0 500 150 150 cm\n/Im1 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": photo(pdf), "Im1": cover})
    return pdf.build(root)



_L_PATH = (
    b"0 0 0 rg\n200 400 m 400 400 l 400 440 l 240 440 l 240 600 l 200 600 l h f\n"
)


def case_204_band_behind_part_of_a_line() -> bytes:
    """A black band behind eight characters in the middle of a line of
    black text. Those characters cannot be read and every one of them
    is in the file.

    The background behind a line was read once for the whole line, so a
    band behind one account number inside a sentence left the line
    mostly on paper and black-on-black went unreported. It has to be
    asked per glyph, like coverage.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = b"0 0 0 rg\n184 457 57 15 re\nf\n" + text_op(
        90,
        460,
        "Reference number 3312-8890 was issued to the former chair in error",
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_205_white_text_in_an_L_notch() -> bytes:
    """White text in the notch of a black L drawn as one concave path.

    The notch is paper, so the text is white on white and nothing of it
    appears. Judge the background by the path's bounding box — which
    covers the notch — and the line reads as white on black, high
    contrast, nothing to report.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = _L_PATH + (
        b"BT\n1 1 1 rg\n/F1 14 Tf\n1 0 0 1 260 520 Tm\n(" + SECRET.encode() + b") Tj\nET\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_206_black_text_in_an_L_notch() -> bytes:
    """#205's guard: black text in the same notch, which anyone can
    read. By the path's bounding box it reads as black on black and a
    perfectly ordinary caption is reported as hidden."""
    pdf = Pdf()
    f = helvetica(pdf)
    body = _L_PATH + (
        b"BT\n0 0 0 rg\n/F1 14 Tf\n1 0 0 1 260 520 Tm\n(Site plan, revision 4) Tj\nET\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_207_transparent_text_over_band() -> bytes:
    """White text at `/ca 0` over a black band: fully transparent ink.

    Nothing of it reaches the page and every word is in the file, which
    is what Tr 3 does and is judged the same way. Read as white on
    black it is high contrast, and A5 passed over it for not being mode
    3, so the line went unreported entirely.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(b"<</Type/ExtGState/ca 0>>")
    body = (
        black_rect(68, 694, 290, 26)
        + b"q\n/GS0 gs\nBT\n1 1 1 rg\n/F1 14 Tf\n1 0 0 1 72 700 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\nQ\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<</GS0 {gs} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_208_text_clip_mode_7() -> bytes:
    """A glyph-shaped clip — `7 Tr`, which adds the letter's outline to
    the clip at ET — and then ordinary text well outside it.

    Nothing of that text reaches the page and all of it extracts. An
    implementation that only watches `W`/`W*` never sees the clip and
    reports the file clean. The letters' own shapes are not available
    here, so what shows inside the clip cannot be established; what is
    outside it certainly does not show, and that is enough for this
    file.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"q\n"
        + b"BT\n7 Tr\n/F1 40 Tf\n1 0 0 1 72 700 Tm\n(X) Tj\nET\n"
        + b"BT\n0 Tr\n0 0 0 rg\n/F1 14 Tf\n1 0 0 1 200 600 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_209_two_clips_in_sequence() -> bytes:
    """A cover clipped to a thin L and then clipped again to the whole
    page, over a plan blacked out everywhere except along the L.

    A second clip intersects with the first; it cannot widen it. Keep
    only the latest and the cover is measured across the page, where
    the picture is already black, and the intact corridor the cover
    really sits on is outvoted.
    """
    pdf = Pdf()
    f = helvetica(pdf)

    def intact(x: float, y: float) -> bool:
        return (202 <= x <= 206 and 200 <= y <= 600) or (
            200 <= x <= 600 and 202 <= y <= 206
        )

    img = _image_from_test(
        pdf, 100, 150.0, 150.0, 500.0, 500.0, lambda x, y: not intact(x, y)
    )
    body = (
        text_op(72, 760, "Plan, one corridor blacked out")
        + b"q\n500 0 0 500 150 150 cm\n/Im0 Do\nQ\n"
        + b"q\n200 200 8 400 re\n200 200 400 8 re\nW n\n"
        + b"0 0 612 792 re\nW n\n"
        + b"0 0 0 rg\n150 150 500 500 re\nf\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)



def _stencil(pdf: Pdf, cells: int, painted) -> int:
    """A stencil mask: one bit per pixel, a 0 bit painting the current
    fill colour. `painted(x, y)` is in mask pixels, row 0 at the top."""
    row_bytes = (cells + 7) // 8
    data = bytearray()
    for y in range(cells):
        row = bytearray(b"\xff" * row_bytes)
        for x in range(cells):
            if painted(x, y):
                row[x >> 3] &= ~(0x80 >> (x & 7)) & 0xFF
        data += row
    return pdf.stream(
        bytes(data),
        f"/Type/XObject/Subtype/Image/Width {cells}/Height {cells}"
        "/ImageMask true/Decode[0 1]",
    )


def case_210_sparse_stencil_over_photo() -> bytes:
    """A 100x100 stencil with a single bit set, laid over a photograph
    in black: it paints one speck, and the photograph is in plain view.

    A mask carries no colour of its own, so its darkness is the fill
    colour's — and taking that alone made every stencil a solid cover
    over its whole rectangle, so a speck on a photograph was reported
    as a blackout with the original picture under it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    mask = _stencil(pdf, 100, lambda x, y: x == 0 and y == 0)
    body = (
        text_op(72, 780, "Photo with a speck on it")
        + b"q\n300 0 0 120 72 620 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 rg\n90 0 0 70 140 650 cm\n/Im1 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": photo(pdf), "Im1": mask})
    return pdf.build(root)


def case_211_solid_stencil_over_photo() -> bytes:
    """The same stencil with every bit set: a solid black block over an
    un-redacted photograph, which is #174's leak drawn as a mask."""
    pdf = Pdf()
    f = helvetica(pdf)
    mask = _stencil(pdf, 32, lambda x, y: True)
    body = (
        text_op(72, 780, "Photo with the face removed")
        + b"q\n300 0 0 120 72 620 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 rg\n90 0 0 70 140 650 cm\n/Im1 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": photo(pdf), "Im1": mask})
    return pdf.build(root)


def case_212_stencil_band_over_ocr() -> bytes:
    """A stencil that paints a solid band across the page, with
    invisible OCR text under the band.

    Half the mask is paper, so the mask as a whole is no cover — and
    the band is. Darkness has to be asked where the text is, for a
    stencil exactly as for a picture. Rows 27-30 of 64 land at y
    441-481; the line sits at y 460.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    mask = _stencil(pdf, 64, lambda x, y: 27 <= y <= 30)
    body = b"q\n0 0 0 rg\n451 0 0 638 72 102 cm\n/Im0 Do\nQ\n" + text_op(
        90, 460, "Account 3312-8890 held by the former chair", tr=3
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": mask})
    return pdf.build(root)


def case_213_stencil_band_over_photo() -> bytes:
    """A stencil painting a solid band over an un-redacted photograph,
    with no text anywhere near it.

    A known gap here: a partly-painted cover is found where text sits
    under it, because that is where darkness is measured. With nothing
    but another picture underneath there is no glyph to measure
    against, and the band — a genuine blackout over a picture nobody
    rewrote — goes unreported. An implementation that decomposes a
    cover into the regions it actually paints fails this file.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    mask = _stencil(pdf, 64, lambda x, y: 20 <= y <= 43)
    body = (
        text_op(72, 780, "Photo with a band across it")
        + b"q\n300 0 0 120 72 620 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 rg\n300 0 0 120 72 620 cm\n/Im1 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": photo(pdf), "Im1": mask})
    return pdf.build(root)



def case_214_text_outside_form_bbox() -> bytes:
    """A Form XObject whose /BBox is 50x50, with text drawn at (100,100)
    inside it: the form's own clip cuts the line away and nothing of it
    reaches the page.

    The library hands a form's /BBox over as a typed array, and a test
    for a plain Array says no to one of those — so the form's clip was
    never applied at all and the line read as text on the page.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    form = pdf.stream(
        b"BT\n/F1 14 Tf\n1 0 0 1 100 100 Tm\n(" + SECRET.encode() + b") Tj\nET\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 50 50]"
        f"/Resources<</Font<</F1 {f} 0 R>>>>",
    )
    body = text_op(72, 760, "Report, with one line held back") + b"q\n/Fm0 Do\nQ\n"
    root, _ = page(pdf, body, font=f, xobjects={"Fm0": form})
    return pdf.build(root)


def case_215_bowtie_clip() -> bytes:
    """A self-crossing clip path — a bow-tie — with text in the gap
    between its two lobes. Nothing of the text is drawn.

    Four points over two x values and two y values also describes a
    rectangle, and treating the bow-tie as its bounding box put the gap
    inside the clip. Its windings also cancel to zero area, so testing
    for a degenerate shape that way calls it empty, which is the
    opposite mistake.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 760, "Plan, one note held back")
        + b"q\n200 400 m 400 600 l 200 600 l 400 400 l h W n\n"
        + b"BT\n0 0 0 rg\n/F1 12 Tf\n1 0 0 1 250 495 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_216_solid_colour_stencil() -> bytes:
    """A 1x1 opaque stencil scaled over an un-redacted photograph.

    The library emits `paintSolidColorImageMask` for this, having
    already established that the stencil paints its whole rectangle —
    so there is no object to look pixels up in, and treating it as a
    stencil nobody could read turned a solid black cover into a cover
    merely needing a look.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    mask = pdf.stream(
        bytes([0x00]),
        "/Type/XObject/Subtype/Image/Width 1/Height 1/ImageMask true/Decode[0 1]",
    )
    body = (
        text_op(72, 780, "Photo with the face removed")
        + b"q\n300 0 0 120 72 620 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 rg\n90 0 0 70 140 650 cm\n/Im1 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": photo(pdf), "Im1": mask})
    return pdf.build(root)


def case_217_nearly_opaque_text() -> bytes:
    """White text at `/ca 0.94` over a black band: not quite opaque,
    and perfectly readable.

    Judging "does this ink paint anything" by the opacity a COVER needs
    reported this line as invisible text. A cover has to be nearly
    opaque to hide anything; text only has to be barely there to be
    read.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(b"<</Type/ExtGState/ca 0.94>>")
    body = (
        black_rect(68, 694, 290, 26)
        + b"q\n/GS0 gs\nBT\n1 1 1 rg\n/F1 14 Tf\n1 0 0 1 72 700 Tm\n"
        + b"(Section 4 annex) Tj\nET\nQ\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<</GS0 {gs} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_218_triangular_hole() -> bytes:
    """A black rectangle with a reverse-wound TRIANGULAR hole in it, and
    text in a corner of that triangle's bounding box — on black, and
    extractable.

    A hole taken out as its bounding box removes black the cover does
    paint, so the corners the box adds read as paper and the text
    standing on them read as black on white: high contrast, nothing to
    report.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 760, "Diagram with one label held back")
        + b"0 0 0 rg\n150 400 m 450 400 l 450 600 l 150 600 l h\n"
        + b"200 430 m 200 570 l 420 570 l h f\n"
        + b"BT\n0 0 0 rg\n/F1 10 Tf\n1 0 0 1 215 440 Tm\n"
        + SECRET.encode().join((b"(", b") Tj\nET\n"))
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_219_text_in_a_rotated_bars_corner() -> bytes:
    """Readable text in the corner that a 45-degree black square's
    bounding box adds, with the square drawn over it.

    The box around a rotated cover reaches text the cover does not, and
    measuring coverage from boxes reported this line as fully covered
    while a reader reads it (123 ink pixels).
    """
    pdf = Pdf()
    f = helvetica(pdf)
    side = 150 * math.cos(math.radians(45))
    body = (
        text_op(72, 760, "Plan with one room blacked out")
        + b"BT\n0 0 0 rg\n/F1 9 Tf\n1 0 0 1 200 410 Tm\n(Room 12) Tj\nET\n"
        + f"q\n{side:.4f} {side:.4f} {-side:.4f} {side:.4f} 300 400 cm\n".encode()
        + b"0 0 0 rg\n0 0 1 1 re\nf\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_220_text_in_a_clipped_covers_gap() -> bytes:
    """Readable text in the white gap between the two side rectangles a
    cover is clipped to, with the cover drawn over it.

    The cover's rectangle spans the gap; its clip does not. Reading a
    cover without its clip said the gap was covered and reported a
    readable total as hidden.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 760, "Table with two columns masked")
        + b"BT\n0 0 0 rg\n/F1 10 Tf\n1 0 0 1 225 455 Tm\n(Total 480) Tj\nET\n"
        + b"q\n150 400 60 120 re\n330 400 60 120 re\nW n\n"
        + b"0 0 0 rg\n150 400 240 120 re\nf\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_221_dotted_rule_over_photo() -> bytes:
    """A round-capped dotted rule — `[0 5] 0 d` — over an un-redacted
    photograph. The dots overlap into a solid black band.

    A zero-length dash is a dot, not nothing: dropping those pieces
    left the rule out of the model entirely, so a band a reader sees as
    solid covered a photograph nobody had touched and the file came
    back clean.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 780, "Photo with a dotted line across it")
        + b"q\n300 0 0 120 72 620 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n20 w\n1 J\n[0 5] 0 d\n100 680 m 340 680 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": photo(pdf)})
    return pdf.build(root)


def case_222_odd_dash_array() -> bytes:
    """`[3] 4 d`: an odd-length dash array, whose real period is twice
    its sum, with a phase that lands inside the first GAP. The image is
    blacked out under the real dashes.

    Reducing the phase modulo the single sum starts the line on ink
    instead, and every dash lands in the wrong place — so a correctly
    prepared document came back with nine findings.
    """
    pdf = Pdf()
    f = helvetica(pdf)

    def covered(x: float, y: float) -> bool:
        if not (490 <= y <= 510):
            return False
        return (x - 100 + 4.0) % 6.0 < 3.0

    pixels = bytearray()
    for row in range(40):
        for col in range(240):
            x = 100 + ((col + 0.5) / 240) * 240
            y = 480 + (1 - (row + 0.5) / 40) * 40
            pixels += b"\x00\x00\x00" if covered(x, y) else bytes((200, 200, 200))
    img = pdf.stream(
        bytes(pixels),
        "/Type/XObject/Subtype/Image/Width 240/Height 40"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    body = (
        text_op(72, 760, "Boundary, drawn with an odd dash array")
        + b"q\n240 0 0 40 100 480 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n20 w\n[3] 4 d\n100 500 m 340 500 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_223_doubling_back_stroke() -> bytes:
    """A 40pt projecting-capped stroke that doubles back on itself —
    `200 500 m 220 500 l 200 500 l S` — with the image blacked out
    under the ink it really lays down.

    Caps go on the ends of an open subpath, not on every segment:
    capping the internal vertex carried the modelled ink 20pt past
    where the stroke turns round, over picture nobody covered.
    """
    pdf = Pdf()
    f = helvetica(pdf)

    def covered(x: float, y: float) -> bool:
        return 178 <= x <= 222 and 478 <= y <= 522

    pixels = bytearray()
    for row in range(40):
        for col in range(80):
            x = 150 + ((col + 0.5) / 80) * 200
            y = 460 + (1 - (row + 0.5) / 40) * 80
            pixels += b"\x00\x00\x00" if covered(x, y) else bytes((200, 200, 200))
    img = pdf.stream(
        bytes(pixels),
        "/Type/XObject/Subtype/Image/Width 80/Height 40"
        "/ColorSpace/DeviceRGB/BitsPerComponent 8",
    )
    body = (
        text_op(72, 760, "Detail, one label blacked out")
        + b"q\n200 0 0 80 150 460 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n40 w\n2 J\n2 j\n200 500 m 220 500 l 200 500 l S\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)



def _alpha_text_page(ca: str, rgb: str = "1 1 1") -> bytes:
    """A line over a black band, painted at the given constant alpha."""
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(f"<</Type/ExtGState/ca {ca}>>".encode())
    body = (
        black_rect(68, 694, 290, 26)
        + b"q\n/GS0 gs\nBT\n"
        + rgb.encode()
        + b" rg\n/F1 14 Tf\n1 0 0 1 72 700 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\nQ\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<</GS0 {gs} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_224_faint_text_over_band() -> bytes:
    """White text at a tenth of full opacity over a black band: nothing
    of it reaches the page (the band stays 100% black on screen) and
    every word is in the file.

    Neither of the old rules caught it. Comparing colours said white
    against black is high contrast; asking whether the alpha was zero
    said it was not. What a reader sees is alpha times the difference
    the ink makes, and a tenth of the way from black to white is under
    the bar.
    """
    return _alpha_text_page("0.1")


def case_225_visible_half_opacity_text() -> bytes:
    """#224's guard: the same line at half opacity, which a reader reads
    without trouble (a tenth of the band's pixels come out light)."""
    return _alpha_text_page("0.5")



def case_226_ring_with_its_hole_filled() -> bytes:
    """A black ring with a second black shape filling its hole, over
    live text: the page is solid black there and every word extracts.

    Taking the holes of every cover out of the coverage of every cover
    let the ring's hole cancel the shape that fills it, and the text
    came out a fiftieth of a percent covered — reported as nothing at
    all. A hole belongs to the cover that has it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 760, "Schedule, one row held back")
        + text_op(200, 500, SECRET)
        # a ring whose hole is a CHAMFERED shape, not a rectangle, so
        # it cannot be taken out of the cover as a box
        + b"0 0 0 rg\n150 450 m 500 450 l 500 560 l 150 560 l h\n"
        + b"190 470 m 170 505 l 190 540 l 480 540 l 480 470 l h f\n"
        # and the hole filled in by a second shape of the same outline
        + b"0 0 0 rg\n190 470 m 170 505 l 190 540 l 480 540 l 480 470 l h f\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_227_soft_masked_text() -> bytes:
    """Text drawn under a fully transparent soft mask, with no cover
    anywhere: nothing of it reaches the page.

    How much a soft mask lets through is a per-pixel matter this walker
    cannot read — unknown, not zero. Writing zero into the alphas made
    the line look like text painted with nothing, which is A5's
    business, and A5 has nothing to say with no cover in sight. So the
    file passed.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    group = pdf.stream(
        b"0 g\n0 0 612 792 re\nf\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 612 792]"
        "/Group<</S/Transparency/CS/DeviceGray>>",
    )
    gs = pdf.add(
        f"<</Type/ExtGState/SMask<</S/Luminosity/G {group} 0 R>>>>".encode()
    )
    body = (
        text_op(72, 760, "Report, with one line held back")
        + b"q\n/GS0 gs\n"
        + text_op(100, 500, SECRET)
        + b"Q\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<</GS0 {gs} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_228_rotated_form_bbox() -> bytes:
    """A Form XObject rotated 45 degrees, with text inside it that
    falls outside the form's own /BBox.

    A rotated box clips to a diamond. Recording the box around that
    diamond adds four corners of page the form never reaches, and the
    line sitting in one of them read as a line on the page.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    form = pdf.stream(
        b"BT\n/F1 12 Tf\n1 0 0 1 110 50 Tm\n(" + SECRET.encode() + b") Tj\nET\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 100 100]"
        f"/Resources<</Font<</F1 {f} 0 R>>>>",
    )
    turn = math.cos(math.radians(45))
    body = (
        text_op(72, 760, "Diagram, one label held back")
        + f"q\n{turn:.5f} {turn:.5f} {-turn:.5f} {turn:.5f} 300 300 cm\n".encode()
        + b"/Fm0 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Fm0": form})
    return pdf.build(root)


def case_229_degenerate_clip_path() -> bytes:
    """A clip path that doubles back on itself — `0 0 m 100 0 l 0 0 l
    0 100 l h` — which encloses nothing, with text inside its bounding
    box.

    Four points over two x values and two y values is not enough to
    make a rectangle: this path has four vertices and no area, and
    taking it for `[0 0 100 100]` said the text inside was on the page
    when the clip removes all of it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 760, "Plan, one note held back")
        + b"q\n0 0 m 100 0 l 0 0 l 0 100 l h W n\n"
        + b"BT\n0 0 0 rg\n/F1 10 Tf\n1 0 0 1 20 50 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_230_cover_clipped_to_a_triangle() -> bytes:
    """Readable text outside a triangle that a black cover is clipped
    to, with the cover drawn over it.

    Clipping the cover to the triangle's bounding box instead of the
    triangle itself put the corners the box adds into the cover, and
    the line standing in one of them was reported as hidden.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(72, 760, "Plan with one corner masked")
        + b"BT\n0 0 0 rg\n/F1 10 Tf\n1 0 0 1 380 560 Tm\n(Room 12) Tj\nET\n"
        + b"q\n200 400 m 440 400 l 200 600 l h W n\n"
        + b"0 0 0 rg\n200 400 240 200 re\nf\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_231_dashed_corner() -> bytes:
    """A dashed stroke turning a corner, with the gap falling exactly
    on the corner, over an image blacked out under the real dashes.

    A dash that merely starts at a vertex has a cap, not a join:
    inventing a mitre there put a wedge of modelled cover 10pt beyond
    the corner, over picture nobody covered.
    """
    pdf = Pdf()
    f = helvetica(pdf)

    def covered(x: float, y: float) -> bool:
        # the vertical dash that starts at the corner, butt-capped
        return 290 <= x <= 310 and 400 <= y <= 420

    img = _image_from_test(pdf, 80, 250.0, 360.0, 120.0, 120.0, covered)
    body = (
        text_op(72, 760, "Route, drawn dashed")
        + b"q\n120 0 0 120 250 360 cm\n/Im0 Do\nQ\n"
        + b"q\n0 0 0 RG\n20 w\n0 J\n0 j\n[10 10] 10 d\n"
        + b"280 400 m 300 400 l 300 420 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_232_pattern_stroked_outline() -> bytes:
    """Mode 2 text over a black band: a black fill and a stroke painted
    with a pattern, which this walker cannot resolve to a colour.

    One unknown ink is enough to make the answer unknown. Judging the
    line on its black fill alone reported it as black on black, while
    the outline is there to read.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    cell = pdf.stream(
        b"1 1 1 rg\n0 0 8 8 re\nf\n",
        "/Type/Pattern/PatternType 1/PaintType 1/TilingType 1"
        "/BBox[0 0 8 8]/XStep 8/YStep 8/Resources<<>>",
    )
    body = (
        black_rect(68, 694, 290, 26)
        + b"BT\n0 0 0 rg\n/Pattern CS\n/P0 SCN\n1 w\n2 Tr\n/F1 20 Tf\n"
        + b"1 0 0 1 72 700 Tm\n(" + SECRET.encode() + b") Tj\nET\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/Pattern<</P0 {cell} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_233_multiply_text() -> bytes:
    """Cyan text in Multiply mode over red: the result is black, which
    a reader sees plainly against the red.

    PDF blends componentwise, and a luminance cannot do that —
    multiplying the two luminances gives a difference of 0.045 and
    calls a readable line invisible. Every mode but Normal answers "not
    known".
    """
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(b"<</Type/ExtGState/BM/Multiply>>")
    body = (
        b"1 0 0 rg\n68 694 290 26 re\nf\n"
        + b"q\n/GS0 gs\nBT\n0 1 1 rg\n/F1 14 Tf\n1 0 0 1 72 700 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\nQ\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<</GS0 {gs} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_234_anisotropic_curve() -> bytes:
    """A curve under `1000 0 0 100`, where the x scale is ten times the
    y, over an image blacked out along the real curve.

    Flattening to the square root of the matrix's determinant takes the
    geometric mean of those two scales, so the chords miss the curve by
    several points on the page. The largest a vector can be stretched
    is what the tolerance has to be measured against.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    start, c1, c2, end = (0.1, 4.0), (0.35, 6.0), (0.35, 2.0), (0.6, 4.0)

    def on_curve(t: float) -> tuple[float, float]:
        u = 1 - t
        return (
            1000 * (u * u * u * start[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t * t * t * end[0]),
            100 * (u * u * u * start[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t * t * t * end[1]),
        )

    along = [on_curve(i / 400) for i in range(401)]

    def covered(x: float, y: float) -> bool:
        return any((x - px) ** 2 + (y - py) ** 2 <= 16.0**2 for px, py in along)

    img = _image_from_test(pdf, 110, 50.0, 330.0, 600.0, 180.0, covered)
    body = (
        text_op(72, 760, "Trace, one stretch blacked out")
        + b"q\n600 0 0 180 50 330 cm\n/Im0 Do\nQ\n"
        + b"q\n1000 0 0 100 0 0 cm\n0 0 0 RG\n0.2 w\n1 J\n"
        + b"0.1 4 m 0.35 6 0.35 2 0.6 4 c\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)



def _smask_page(mask_grey: bytes, extra_off: bool = False) -> bytes:
    """Live text, then a black cover drawn under a luminosity soft mask
    whose group paints `mask_grey` over the whole page."""
    pdf = Pdf()
    f = helvetica(pdf)
    group = pdf.stream(
        mask_grey + b"\n0 0 612 792 re\nf\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 612 792]"
        "/Group<</S/Transparency/CS/DeviceGray>>",
    )
    on = pdf.add(
        f"<</Type/ExtGState/SMask<</S/Luminosity/G {group} 0 R>>>>".encode()
    )
    off = pdf.add(b"<</Type/ExtGState/SMask/None>>")
    body = text_op(72, 700, SECRET) + (
        b"q\n/GS1 gs\n" + (b"/GS0 gs\n" if extra_off else b"")
        + black_rect(68, 694, 290, 26)
        + b"Q\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    states = f"/GS1 {on} 0 R" + (f"/GS0 {off} 0 R" if extra_off else "")
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<<{states}>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_235_smask_then_none() -> bytes:
    """A soft mask turned on, turned off again with `/SMask /None`, and
    then an ordinary opaque black cover over live text.

    The library writes `false` for /SMask /None, as well as null and
    the name itself. Testing only the last two left the mask in force
    for the rest of the page, so a plain blackout was modelled as one
    nobody could judge and the covered line went unreported.
    """
    return _smask_page(b"0 g", extra_off=True)


def case_236_smasked_cover() -> bytes:
    """Live text under a black cover drawn through a fully opaque
    luminosity mask: the line is hidden on the page and extractable.

    How much a soft mask lets through is a per-pixel matter this walker
    cannot read, so the cover is never a blackout — but it is still a
    cover. Dropping it from both lists let the hidden line go
    unreported entirely.
    """
    return _smask_page(b"1 g")


def case_237_collinear_clip_window() -> bytes:
    """A black cover clipped to a convex window written with three
    collinear points along one edge, over live text.

    Choosing which side of an edge is "inside" from the next vertex
    alone gives nothing when that vertex is on the edge, and the window
    came out inside-out: the cover was reduced to a line of no area and
    covered nothing.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        text_op(200, 500, SECRET)
        + b"q\n200 480 m 200 500 l 200 520 l 460 520 l 460 480 l h W n\n"
        + b"0 0 0 rg\n150 450 400 120 re\nf\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_238_pentagram_clip() -> bytes:
    """A black cover clipped to a pentagram, with text over one of its
    arms.

    A pentagram turns the same way at every point and goes round twice,
    so a test for consistent turns calls it convex; clipping against it
    edge by edge then keeps only its middle and leaves the arms — which
    are painted — out of the cover.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    points = []
    for i in range(5):
        angle = 2 * math.pi * ((i * 2) % 5) / 5 + math.pi / 2
        points.append((300 + 120 * math.cos(angle), 500 + 120 * math.sin(angle)))
    star = f"{points[0][0]:.2f} {points[0][1]:.2f} m " + " ".join(
        f"{x:.2f} {y:.2f} l" for x, y in points[1:]
    ) + " h"
    body = (
        b"BT\n0 0 0 rg\n/F1 9 Tf\n1 0 0 1 285 600 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
        + b"q\n"
        + star.encode()
        + b" W n\n0 0 0 rg\n150 350 300 300 re\nf\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_239_bowtie_fill_over_text() -> bytes:
    """A black self-crossing fill — a bow-tie — over live text, with the
    text running through one of its lobes.

    The windings cancel, so the area comes out near nothing and the
    shape was not rectangular enough to be a cover at all: a lobe of
    solid black over a line, reported as nothing. The hull stands in as
    a bound, which carries it to a look rather than to either answer.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 10 Tf\n1 0 0 1 250 470 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
        + b"0 0 0 rg\n200 400 m 400 600 l 200 600 l 400 400 l h f\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)



def case_240_uneven_bowtie_fill() -> bytes:
    """A self-crossing fill whose two lobes are different sizes, over
    live text standing in one of them.

    Equal lobes cancel to no area at all, which is a signal; unequal
    ones leave some, so a test on the area let this one through as an
    exact shape. It then failed the rectangularity bar and was dropped
    from every cover list — a lobe of solid black over a line,
    reported as nothing. Crossings are asked about directly now, and a
    shape that could not be established is a bound, which cannot be
    used to rule the paint out.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 9 Tf\n1 0 0 1 220 520 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
        + b"0 0 0 rg\n200 450 m 400 550 l 200 530 l 360 450 l h f\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_241_clipped_hole_cover() -> bytes:
    """A cover with a triangular hole on its right, clipped to a strip
    on its left, over one large glyph.

    The clip excludes the hole entirely, so the strip covers the
    glyph's left fifth outright. Subtracting the hole from the cover's
    area anyway — without putting it through the same clip — took the
    whole cover away and the partly-covered glyph was reported as
    nothing.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 100 Tf\n1 0 0 1 200 450 Tm\n(H) Tj\nET\n"
        + b"q\n200 450 20 100 re\nW n\n"
        + b"0 0 0 rg\n200 450 m 300 450 l 300 550 l 200 550 l h\n"
        + b"220 450 m 300 450 l 300 550 l h f\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_242_smasked_image_cover() -> bytes:
    """A black image drawn through a fully transparent luminosity mask
    over a visible heading: the heading reads perfectly.

    The per-glyph darkness measurement promoted any locally dark image
    to a blackout, going round the opacity question rather than
    through it, and a heading nobody had covered was reported as
    covered.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    group = pdf.stream(
        b"0 g\n0 0 612 792 re\nf\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 612 792]"
        "/Group<</S/Transparency/CS/DeviceGray>>",
    )
    on = pdf.add(
        f"<</Type/ExtGState/SMask<</S/Luminosity/G {group} 0 R>>>>".encode()
    )
    block = black_block(pdf)
    body = text_op(72, 700, "Visible heading, nothing hidden") + (
        b"q\n/GS1 gs\n290 0 0 26 68 694 cm\n/Im0 Do\nQ\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/XObject<</Im0 {block} 0 R>>"
            f"/ExtGState<</GS1 {on} 0 R>>>>/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_243_dash_gap_at_the_corner() -> bytes:
    """A dashed stroke whose gap reaches a corner, so the next dash only
    STARTS there, with readable text just past the corner.

    Reading the dash state after the gap ends says "on", and a join was
    added although the ink begins at the corner with a cap. The wedge
    that invented covered the text beside it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 6 Tf\n1 0 0 1 222 492 Tm\n(Room 12) Tj\nET\n"
        + b"q\n0 0 0 RG\n20 w\n0 J\n0 j\n[10 10] 0 d\n"
        + b"200 500 m 220 500 l 220 520 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_244_dash_through_the_corner() -> bytes:
    """A dash that runs THROUGH a corner, with a glyph just outside the
    join, under projecting caps.

    A dash continuing through a vertex has a join there and no caps.
    Giving every dash fragment caps at both ends added two squares of
    ink the page never has, which covered the glyph beside the corner.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 4 Tf\n1 0 0 1 310.8 497.6 Tm\n(x) Tj\nET\n"
        + b"q\n0 0 0 RG\n4 w\n2 J\n2 j\n[15 10] 0 d\n"
        + b"300 500 m 310 500 l 310 510 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)



def case_245_norotate_annot() -> bytes:
    """A black Square annotation with /F 20 — Print and NoRotate — on a
    /Rotate 90 page, over a line of text.

    A NoRotate annotation stays upright while the page turns, rotating
    about its /Rect's upper-left corner, so where it lands is not where
    its /Rect says. Place it without that correction and the line it
    hides on screen reads as uncovered. (pdf.js applies the correction
    in the operator list, so an implementation built on it inherits
    the right placement; the fixture is here to keep that true.)
    """
    pdf = Pdf()
    f = helvetica(pdf)
    annot = pdf.add(
        b"<</Type/Annot/Subtype/Square/Rect[68 694 358 720]"
        b"/IC[0 0 0]/C[0 0 0]/CA 1/F 20>>"
    )
    root, _ = page(
        pdf,
        text_op(72, 700, SECRET),
        font=f,
        annots=f"/Annots[{annot} 0 R]",
        extra_page="/Rotate 90",
    )
    return pdf.build(root)


def case_246_ap_state_without_as() -> bytes:
    """A Square annotation whose /AP /N is a STATE DICTIONARY — one
    black appearance under /Off — with no /AS to say which state is
    showing.

    Viewers disagree: one picks the state from /V, then the parent's
    /V, then /Off, and draws the black box over the line; another draws
    nothing. Whichever this tool's renderer does, the honest answer is
    that an appearance it could not resolve may be hiding the line, so
    the file earns a look rather than a pass.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    appearance = pdf.stream(
        b"0 0 0 rg\n0 0 290 26 re\nf\n",
        "/Type/XObject/Subtype/Form/BBox[0 0 290 26]",
    )
    annot = pdf.add(
        f"<</Type/Annot/Subtype/Square/Rect[68 694 358 720]"
        f"/AP<</N<</Off {appearance} 0 R>>>>/F 4>>".encode()
    )
    root, _ = page(
        pdf, text_op(72, 700, SECRET), font=f, annots=f"/Annots[{annot} 0 R]"
    )
    return pdf.build(root)


def case_247_empty_stencil_over_text() -> bytes:
    """An 8x8 stencil with no bits set, drawn in black over a line of
    text: it paints nothing at all, and the line reads perfectly.

    A mask takes its darkness from the fill colour, and taking that
    alone made its whole rectangle a cover — so a stencil that paints
    nothing covered every word of the line. A stencil covers where it
    paints, which is the same rule A6 uses.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    mask = _stencil(pdf, 8, lambda x, y: False)
    body = text_op(72, 700, SECRET) + (
        b"q\n0 0 0 rg\n290 0 0 26 68 694 cm\n/Im0 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": mask})
    return pdf.build(root)



def case_248_terminal_dash_cap() -> bytes:
    """A dashed stroke whose last dash ends where the path does, with a
    round cap, and a glyph just past that end.

    A dash with length still to run carries on into the next segment —
    unless there is no next segment, which is where an open path stops
    and the cap goes on. Asking only whether the dash had length left
    lost the terminal cap, and the round cap a reader sees over the
    glyph went unmodelled.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 6 Tf\n1 0 0 1 222 497 Tm\n(x) Tj\nET\n"
        + b"q\n0 0 0 RG\n20 w\n1 J\n[100 20] 0 d\n200 500 m 220 500 l\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_249_filled_and_stroked_faint() -> bytes:
    """Mode 2 text with the fill AND the stroke at a tenth of opacity
    each, on white paper.

    Neither ink reaches the visibility bar on its own — each moves the
    page a tenth of the way from white to black — but they are laid one
    over the other where they overlap, so the page moves twice and the
    outline is plainly there to read (12.5% of the line's box carries
    ink, the darkest pixel 0.82). Judging the inks one at a time called
    a readable line hidden.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(b"<</Type/ExtGState/ca 0.1/CA 0.1>>")
    body = (
        b"q\n/GS0 gs\nBT\n0 0 0 rg\n0 0 0 RG\n0.7 w\n2 Tr\n/F1 20 Tf\n"
        b"1 0 0 1 72 700 Tm\n(" + SECRET.encode() + b") Tj\nET\nQ\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<</GS0 {gs} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_250_explicit_closing_point() -> bytes:
    """A correct redaction under a rectangle that writes its closing
    point explicitly before `h`.

    `200 490 m 280 490 l 280 520 l 200 520 l 200 490 l h` is an
    ordinary rectangle. Testing for a self-crossing outline by index
    alone made its first and closing edges — which meet at the start
    point — look like a crossing, so the rectangle was marked as a
    shape that could not be established and a correct redaction under
    it became a cover nobody could judge. Edges that share a point
    meet there by construction.
    """
    pdf = Pdf()
    f = helvetica(pdf)

    def covered(x: float, y: float) -> bool:
        return 198 <= x <= 282 and 488 <= y <= 522

    img = _image_from_test(pdf, 40, 180.0, 470.0, 120.0, 70.0, covered)
    body = (
        text_op(72, 760, "Detail, one label blacked out")
        + b"q\n120 0 0 70 180 470 cm\n/Im0 Do\nQ\n"
        + b"0 0 0 rg\n200 490 m 280 490 l 280 520 l 200 520 l 200 490 l h f\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)



def case_251_explicit_normal_blend() -> bytes:
    """A black bar over live text, with `/BM /Normal` stated
    explicitly in an /ExtGState.

    Plenty of producers write that line, and it changes nothing about
    what the page looks like — but a checker comparing the library's
    blend name against the PDF one matched neither, read the paint as
    not opaque, and the bar was no cover at all. The plainest leak in
    the corpus, hidden behind one word.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(b"<</Type/ExtGState/BM/Normal>>")
    body = text_op(72, 700, SECRET) + (
        b"q\n/GS0 gs\n" + black_rect(68, 694, 290, 26) + b"Q\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<</GS0 {gs} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_252_closed_dash_terminal_cap() -> bytes:
    """A closed dashed square whose phase starts in a gap, with a glyph
    under the round cap where the dashes run out.

    A closed path's last dash runs into its first only if there was ink
    there to run into. Treating "the path is closed" as proof of a join
    lost the terminal cap, and the glyph that cap covers with it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 6 Tf\n1 0 0 1 201 493 Tm\n(x) Tj\nET\n"
        + b"q\n0 0 0 RG\n20 w\n1 J\n2 j\n[30 30] 45 d\n"
        + b"200 500 m 220 500 l 220 520 l 200 520 l h S\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_253_crossing_named_vertex() -> bytes:
    """A self-crossing fill that names its crossing point as a vertex,
    over live text in one lobe.

    Skipping every pair of edges that share a point — to forgive a
    path that writes its closing point twice — also forgave the pair
    that meets AT the crossing, which is how a crossing is written when
    the path names it. The two lobes then cancelled and the fill was
    dropped as not rectangular, so solid black over a line was
    reported as nothing.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 8 Tf\n1 0 0 1 260 560 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
        + b"0 0 0 rg\n200 400 m 300 500 l 400 600 l 200 600 l 300 500 l"
        + b" 400 400 l h f\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_254_diamond_hole() -> bytes:
    """A black rectangle with a DIAMOND-shaped hole, and text outside
    the diamond but inside its bounding box.

    An oriented rectangle counts as rectangular, so the diamond went
    into the list of holes taken out as boxes — which removed painted
    corners, and the text standing on black in one of them read as
    text on paper.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 8 Tf\n1 0 0 1 230 430 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
        + b"0 0 0 rg\n200 400 m 400 400 l 400 600 l 200 600 l h\n"
        + b"225 500 m 300 575 l 375 500 l 300 425 l h f\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_255_nested_island() -> bytes:
    """Three nested rectangles filled with `f*`, and text on the
    innermost one.

    Under the even-odd rule the outer ring and the innermost island are
    both painted; the ring between them is not. Asking only "is
    anything around me" made the island a hole, so the solid black it
    paints over the line was reported as nothing.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 8 Tf\n1 0 0 1 285 495 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
        + b"0 0 0 rg\n200 400 200 200 re\n230 430 140 140 re\n"
        + b"270 470 60 60 re\nf*\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)



def case_256_shape_in_an_L_notch() -> bytes:
    """An independent black rectangle sitting in the notch of an
    L-shaped subpath, both filled in one `f*`, over live text.

    The rectangle is inside the L's bounding BOX and outside the L
    itself, so deciding what encloses what from boxes made it the L's
    hole and threw it away — and the text it really covers was
    reported as nothing. What encloses what is a question about the
    shapes.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 8 Tf\n1 0 0 1 300 500 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
        + b"0 0 0 rg\n200 400 m 400 400 l 400 450 l 250 450 l 250 600 l"
        + b" 200 600 l h\n280 480 200 40 re\nf*\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_257_winding_island() -> bytes:
    """Three nested rectangles wound positive, negative, positive and
    filled with `f`, with text on the innermost one.

    Under the nonzero rule the innermost winds to 1 and paints; asking
    only "does any enclosing subpath wind the other way" made it a hole
    and the solid black it lays over the line was reported as nothing.
    Winding numbers add up.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 8 Tf\n1 0 0 1 285 495 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
        + b"0 0 0 rg\n"
        + b"200 400 m 400 400 l 400 600 l 200 600 l h\n"
        + b"230 430 m 230 570 l 370 570 l 370 430 l h\n"
        + b"270 470 m 330 470 l 330 530 l 270 530 l h\nf\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_258_white_multiply_over_band() -> bytes:
    """A black band, an opaque WHITE rectangle over it under
    `/BM /Multiply`, then black text on top.

    Multiply keeps whichever is darker, so the white rectangle leaves
    the band black and the black text on it cannot be read. Taking the
    rectangle's declared colour as the new background said the text was
    black on white — high contrast, nothing to report. A paint composes
    with what is behind it; it does not replace it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(b"<</Type/ExtGState/BM/Multiply>>")
    body = (
        black_rect(68, 694, 290, 26)
        + b"q\n/GS0 gs\n1 1 1 rg\n68 694 290 26 re\nf\nQ\n"
        + text_op(72, 700, SECRET)
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<</GS0 {gs} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_259_screen_image_over_text() -> bytes:
    """A black image laid over visible text under `/BM /Screen`.

    Screen keeps whichever is lighter, so by the format a black image
    in that mode hides nothing — and pdf.js paints it black anyway.
    Whichever a reader's viewer does, a cover under a blend mode this
    tool does not model is a cover it cannot speak for: a look, never
    a confident failure and never silence.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(b"<</Type/ExtGState/BM/Screen>>")
    block = black_block(pdf)
    body = text_op(72, 700, "Visible heading, nothing hidden") + (
        b"q\n/GS0 gs\n290 0 0 26 68 694 cm\n/Im0 Do\nQ\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/XObject<</Im0 {block} 0 R>>"
            f"/ExtGState<</GS0 {gs} 0 R>>>>/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_260_closed_dash_wrap_cap() -> bytes:
    """A closed dashed path whose ink runs through its closing point,
    with a readable glyph just outside that corner.

    The dash that arrives runs into the piece that left, so the corner
    has a join and the piece that left has no start cap. Leaving its
    cap on added a projecting square of ink the path does not paint,
    over the glyph beside it.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 5 Tf\n1 0 0 1 192 498 Tm\n(x) Tj\nET\n"
        + b"q\n0 0 0 RG\n20 w\n2 J\n[300 100] 0 d\n200 500 m 300 500 l h S\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_261_two_identical_diamonds() -> bytes:
    """The same black diamond drawn twice over one glyph, each covering
    about a seventh of its box.

    Adding each shape's area cell by cell counts the overlap twice
    where two shapes meet inside a cell without introducing a grid
    line, so one seventh became two and a cover that is not there was
    reported. A few convex shapes can be unioned exactly, by inclusion
    and exclusion.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    diamond = b"200 503.15 m 202.15 501 l 200 498.85 l 197.85 501 l h"
    body = (
        b"BT\n0 0 0 rg\n/F1 10 Tf\n1 0 0 1 196 498 Tm\n(X) Tj\nET\n"
        + b"0 0 0 rg\n"
        + diamond
        + b" f\n"
        + diamond
        + b" f\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)



def case_262_overlapping_subpaths() -> bytes:
    """Two rectangles that OVERLAP — not nest — filled in one `f*`,
    with text where the second one paints alone.

    Under even-odd their overlap cancels and the rest of each paints,
    so one subpath has parts the fill rule answers differently and no
    single point inside it can speak for both. Which part a sample
    lands in depends on the order the vertices were written, and the
    rectangle that covers the line was being dropped as unpainted. A
    tangle like this is reported as a bound: a look, never silence.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 8 Tf\n1 0 0 1 402 448 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
        + b"0 0 0 rg\n200 400 200 100 re\n"
        + b"390 440 m 390 460 l 410 460 l 410 440 l h f*\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_263_nearly_opaque_band() -> bytes:
    """A black rectangle at `/ca 0.9` over white paper, with black text
    on top of it.

    The page comes out at a tenth of the way to white, so the black
    text on it is below the contrast a reader needs. The background was
    reconstructed by skipping any paint below the opacity a BLACKOUT
    needs — which is a different question — so the text was compared
    with white paper and the contrast found was not there.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(b"<</Type/ExtGState/ca 0.9>>")
    body = (
        b"q\n/GS0 gs\n" + black_rect(68, 694, 290, 26) + b"Q\n"
        + text_op(72, 700, SECRET)
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<</GS0 {gs} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))


def case_264_white_text_on_screen_image() -> bytes:
    """A black image under `/BM /Screen` on white paper, with white
    text over it.

    By the format Screen keeps whichever is lighter, so the page stays
    white and the white text on it cannot be read; pdf.js paints the
    image black, where it can. Replacing the background with the
    image's own pixels ignored both its alpha and its blend and
    reported nothing either way.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(b"<</Type/ExtGState/BM/Screen>>")
    block = black_block(pdf)
    body = (
        b"q\n/GS0 gs\n290 0 0 26 68 694 cm\n/Im0 Do\nQ\n"
        + b"BT\n1 1 1 rg\n/F1 14 Tf\n1 0 0 1 72 700 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/XObject<</Im0 {block} 0 R>>"
            f"/ExtGState<</GS0 {gs} 0 R>>>>/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))



def case_265_bar_across_a_U_notch() -> bytes:
    """A bar whose two ends sit in the arms of a U-shaped subpath,
    crossing its notch, both filled with `f*`.

    Every vertex of the bar is inside the U, and the bar still crosses
    it — a concave shape can contain all of something's corners and
    none of its middle. Reading that as nesting made the bar the U's
    hole and dropped it, and the notch it paints black over the text
    was reported as nothing.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 6 Tf\n1 0 0 1 294 482 Tm\n(X) Tj\nET\n"
        + b"0 0 0 rg\n"
        + b"200 400 m 400 400 l 400 500 l 310 500 l 310 420 l 290 420 l"
        + b" 290 500 l 200 500 l h\n210 480 180 10 re\nf*\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_266_terminal_dash_dot() -> bytes:
    """`[5 5 0 5]` on a ten-unit line: the pattern reaches its
    zero-length "on" element exactly at the end, which paints a dot
    there under a round cap, over a glyph beside it.

    The walk stopped when the line ran out and never emitted that dot,
    so the ink a reader sees over the glyph went unmodelled.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 5 Tf\n1 0 0 1 112 498 Tm\n(X) Tj\nET\n"
        + b"q\n0 0 0 RG\n10 w\n1 J\n[5 5 0 5] 0 d\n100 500 m 110 500 l S\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_267_stroke_swallows_fill() -> bytes:
    """Mode 2 text with a black fill and a 100pt WHITE stroke: the
    stroke is painted after the fill and swallows it, so nothing of the
    line reaches the page (0% ink, the darkest pixel 0.90).

    Letting any one ink's contrast settle the question called the line
    readable on the strength of a fill nobody can see. Where the stroke
    is narrow against the glyph both inks show, which is the ordinary
    white-halo style, and that still passes.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n1 1 1 RG\n100 w\n2 Tr\n/F1 20 Tf\n"
        b"1 0 0 1 72 700 Tm\n(" + SECRET.encode() + b") Tj\nET\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_268_half_transparent_image() -> bytes:
    """A black image with a constant /SMask of 128 laid over a visible
    heading: it renders mid-grey and the heading reads through it.

    Decoded pixels arrive as RGBA with alpha 128, and treating a pixel
    as either transparent or solid at the halfway mark made them solid
    black — a cover that hides nothing, reported as one that does.
    Composed over paper by their own alpha they are mid-grey, which is
    no blackout; the cover still earns a look, since how much it hides
    is a matter of degree.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    smask = pdf.stream(
        bytes([128] * 16),
        "/Type/XObject/Subtype/Image/Width 4/Height 4"
        "/ColorSpace/DeviceGray/BitsPerComponent 8",
    )
    img = pdf.stream(
        bytes([0] * 48),
        "/Type/XObject/Subtype/Image/Width 4/Height 4"
        f"/ColorSpace/DeviceRGB/BitsPerComponent 8/SMask {smask} 0 R",
    )
    body = text_op(72, 700, "Visible heading, nothing hidden") + (
        b"q\n290 0 0 26 68 694 cm\n/Im0 Do\nQ\n"
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root)


def case_269_white_over_unknown_blend() -> bytes:
    """A black rectangle in a blend mode this tool does not model, an
    opaque white rectangle over it, then white text.

    The page is certainly white there, so the text is certainly
    invisible. An opaque paint in Normal replaces whatever was under
    it, known or not — and keeping "not known" from the earlier paint
    turned a definite white-on-white finding into a look.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    gs = pdf.add(b"<</Type/ExtGState/BM/Screen>>")
    body = (
        b"q\n/GS0 gs\n" + black_rect(68, 694, 290, 26) + b"Q\n"
        + b"1 1 1 rg\n68 694 290 26 re\nf\n"
        + b"BT\n1 1 1 rg\n/F1 14 Tf\n1 0 0 1 72 700 Tm\n("
        + SECRET.encode()
        + b") Tj\nET\n"
    )
    pages_num = pdf.reserve()
    page_num = pdf.reserve()
    contents = pdf.stream(body)
    pdf.put(
        page_num,
        (
            f"<</Type/Page/Parent {pages_num} 0 R/MediaBox[0 0 {PAGE_W} {PAGE_H}]"
            f"/Resources<</Font<</F1 {f} 0 R>>/ExtGState<</GS0 {gs} 0 R>>>>"
            f"/Contents {contents} 0 R>>"
        ).encode(),
    )
    pdf.put(pages_num, f"<</Type/Pages/Kids[{page_num} 0 R]/Count 1>>".encode())
    return pdf.build(pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode()))



def case_270_far_control_curve() -> bytes:
    """A 20pt stroke along a curve whose control points are hundreds of
    thousands of units off the page, with a glyph under where the real
    curve runs.

    Sixteen chords through such a curve peak a hundred points below it,
    so an extent built from the points this walker sampled does not
    reach the ink — and the coverage test rejects a paint by its extent
    before it ever looks at the shape. A Bézier lies inside the convex
    hull of its control points, which bounds it exactly where an error
    estimate did not.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 6 Tf\n1 0 0 1 300 486 Tm\n(X) Tj\nET\n"
        + b"q\n0 0 0 RG\n20 w\n"
        + b"100 -422000 m 233.333 132666.667 366.667 141200 500 -396400 c\nS\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)



def _no_widths_font(pdf: Pdf) -> int:
    """A font the file neither embeds nor names from the standard
    fourteen, with no /Widths: every viewer substitutes a face and
    guesses the advances."""
    return pdf.add(
        b"<</Type/Font/Subtype/TrueType/BaseFont/NotARealFaceMT"
        b"/FirstChar 32/LastChar 255/Encoding/WinAnsiEncoding>>"
    )


def case_271_estimated_positions_clear() -> bytes:
    """A line in a font with no widths, measured CLEAR of a black box
    on the same page.

    Where the file leaves a font's advances to the viewer, the position
    of every letter is that viewer's estimate — and two viewers
    substituting different faces put the tail of a line in different
    places. A cover decision taken on one of them does not carry to a
    reader using another, so a page with such a font, a cover on it,
    and nothing reported about the text earns a look rather than a
    pass.
    """
    pdf = Pdf()
    font = _no_widths_font(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 20 Tf\n1 0 0 1 80 700 Tm\n(SECRET) Tj\nET\n"
        + black_rect(280, 694, 200, 30)
    )
    root, _ = page(pdf, body, font=font)
    return pdf.build(root)


def case_272_font_without_widths() -> bytes:
    """The same font with a longer line, whose tail this renderer's
    substituted metrics put under the box.

    #271's companion: a definite finding stands on its own and needs no
    look beside it. Another viewer's metrics may put the tail
    elsewhere, which is what #271 is about.
    """
    pdf = Pdf()
    font = _no_widths_font(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 20 Tf\n1 0 0 1 80 700 Tm\n(WWWWWWWWWWSECRET) Tj\nET\n"
        + black_rect(280, 694, 200, 30)
    )
    root, _ = page(pdf, body, font=font)
    return pdf.build(root)


def case_273_encrypt_direct_dictionary() -> bytes:
    """RC4 encryption whose /Encrypt is written into the trailer as a
    dictionary rather than as an indirect reference, with a line of
    text under a black box.

    A reader that recognises only the reference form opens the file,
    never decrypts it, and finds an empty page — which every check
    passes over. pdf.js handles both forms, so this engine reports the
    covered line either way; the fixture is here to keep that true.
    """
    return _encrypted_pdf_with_cover(direct=True)


def case_274_encrypt_indirect_reference() -> bytes:
    """#273 with the usual indirect /Encrypt, as the guard that the two
    forms are read the same way."""
    return _encrypted_pdf_with_cover(direct=False)


def _encrypted_pdf_with_cover(direct: bool) -> bytes:
    """#273/#274: a readable line under a black box, behind RC4 with an
    empty user password, written by hand so the /Encrypt entry can take
    either form."""

    def padded(pw: str) -> bytes:
        return (pw.encode("latin-1") + _PW_PAD)[:32]

    owner_entry = _rc4(hashlib.md5(padded("owner")).digest()[:5], padded(""))
    digest = hashlib.md5()
    digest.update(padded(""))
    digest.update(owner_entry)
    digest.update(struct.pack("<i", _ENC_P))
    digest.update(_ENC_ID)
    file_key = digest.digest()[:5]
    user_entry = _rc4(file_key, _PW_PAD)

    def object_key(num: int, gen: int = 0) -> bytes:
        extra = bytes(
            [num & 0xFF, (num >> 8) & 0xFF, (num >> 16) & 0xFF, gen & 0xFF, (gen >> 8) & 0xFF]
        )
        return hashlib.md5(file_key + extra).digest()[:10]

    body = (
        b"BT /F1 14 Tf 72 700 Td (" + SECRET.encode() + b") Tj ET\n"
        + b"0 0 0 rg 68 694 290 26 re f\n"
    )
    stream = _rc4(object_key(5), body)
    encrypt = (
        b"<</Filter/Standard/V 1/R 2/O <"
        + owner_entry.hex().encode("ascii")
        + b">/U <"
        + user_entry.hex().encode("ascii")
        + b">/P "
        + str(_ENC_P).encode("ascii")
        + b">>"
    )
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
    }
    if not direct:
        objects[6] = encrypt

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
        + b"/Root 1 0 R/Encrypt "
        + (encrypt if direct else b"6 0 R")
        + b"/ID[<"
        + ident
        + b"><"
        + ident
        + b">]>>\nstartxref\n"
        + str(xref).encode("ascii")
        + b"\n%%EOF\n"
    )
    return bytes(out)



def case_275_bar_with_named_crossings() -> bytes:
    """A bar laid across the notch of a U, with its intersections with
    the U written in as vertices, both filled with `f*`.

    Every corner of the bar is then inside the U and no edge PROPERLY
    crosses one — the edges touch at those vertices — so a tangle test
    that asks about crossings alone took the bar for a hole and dropped
    it, and the black it paints across the notch over the text was
    reported as nothing. Two subpaths whose interiors meet without one
    being inside the other are a tangle however the path is written.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 7 Tf\n1 0 0 1 196 246 Tm\n(X) Tj\nET\n"
        + b"0 0 0 rg\n"
        + b"100 100 m 300 100 l 300 300 l 220 300 l 220 200 l 180 200 l"
        + b" 180 300 l 100 300 l h\n"
        + b"120 240 m 180 240 l 220 240 l 280 240 l 280 260 l 220 260 l"
        + b" 180 260 l 120 260 l h f*\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_276_zero_length_dot() -> bytes:
    """`200 500 m 200 500 l S` with a 20pt round cap: a dot, over a
    glyph.

    Collapsing the repeated point left no segment to paint the cap
    from, so the dot a reader sees went unmodelled entirely.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n/F1 7 Tf\n1 0 0 1 198 497 Tm\n(X) Tj\nET\n"
        + b"q\n0 0 0 RG\n20 w\n1 J\n200 500 m 200 500 l S\nQ\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_277_stretched_text_matrix() -> bytes:
    """Mode 2 text under a text matrix that stretches tenfold ACROSS the
    line, with a 5pt white stroke over a black fill.

    A matrix that stretches the line sideways does not widen the stroke
    across the stems it outlines, and taking the largest stretch the
    matrix can apply called a 5pt stroke 50pt, decided it had swallowed
    the fill, and reported a line that is plainly there (13.9% of its
    box carries ink) as hidden.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    body = (
        b"BT\n0 0 0 rg\n1 1 1 RG\n5 w\n2 Tr\n/F1 20 Tf\n"
        b"10 0 0 1 40 700 Tm\n(" + SECRET.encode() + b") Tj\nET\n"
    )
    root, _ = page(pdf, body, font=f)
    return pdf.build(root)


def case_278_half_image_over_black_band() -> bytes:
    """An opaque black band, a half-transparent black image over it,
    then black text: the band shows through and the text cannot be
    read.

    Flattening the image's pixels over paper before handing them on
    made them mid-grey, which then REPLACED the black band — so the
    text read as black on grey, with contrast enough to pass. A pixel's
    colour and its alpha have to stay apart until there is something to
    lay it over.
    """
    pdf = Pdf()
    f = helvetica(pdf)
    smask = pdf.stream(
        bytes([128] * 16),
        "/Type/XObject/Subtype/Image/Width 4/Height 4"
        "/ColorSpace/DeviceGray/BitsPerComponent 8",
    )
    img = pdf.stream(
        bytes([0] * 48),
        "/Type/XObject/Subtype/Image/Width 4/Height 4"
        f"/ColorSpace/DeviceRGB/BitsPerComponent 8/SMask {smask} 0 R",
    )
    body = (
        black_rect(68, 694, 290, 26)
        + b"q\n290 0 0 26 68 694 cm\n/Im0 Do\nQ\n"
        + text_op(72, 700, SECRET)
    )
    root, _ = page(pdf, body, font=f, xobjects={"Im0": img})
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
    "147-two-bands-over-every-glyph": case_147_two_bands_over_every_glyph,
    "148-rotated-bar-over-text": case_148_rotated_bar_over_text,
    "149-rotated-image-cover": case_149_rotated_image_cover,
    "150-triangular-clip": case_150_triangular_clip,
    "151-triangular-clip-inside": case_151_triangular_clip_inside,
    "152-annotation-author-and-rich-text": case_152_annotation_author_and_rich_text,
    "153-rotated-clip-corner": case_153_rotated_clip_corner,
    "154-rotated-clip-inside": case_154_rotated_clip_inside,
    "155-inline-image-decode-inverted": case_155_inline_image_decode_inverted,
    "156-inline-image-white": case_156_inline_image_white,
    "157-text-field-value-off": case_157_text_field_value_off,
    "158-glyph-names-without-tounicode": case_158_glyph_names_without_tounicode,
    "159-outline-signature-openaction-xfa": case_159_outline_signature_openaction_xfa,
    "160-concave-clip-notch": case_160_concave_clip_notch,
    "161-concave-clip-lower-arm": case_161_concave_clip_lower_arm,
    "162-concave-clip-right-arm": case_162_concave_clip_right_arm,
    "163-inline-image-filter-chain": case_163_inline_image_filter_chain,
    "164-inline-image-unknown-filter": case_164_inline_image_unknown_filter,
    "165-clip-edge-tangent": case_165_clip_edge_tangent,
    "166-clip-edge-into-descent": case_166_clip_edge_into_descent,
    "167-clip-edge-clear-of-the-box": case_167_clip_edge_clear_of_the_box,
    "168-clip-cuts-the-descent-band": case_168_clip_cuts_the_descent_band,
    "169-clip-leaves-an-ascender-sliver": case_169_clip_leaves_an_ascender_sliver,
    "170-overlapping-clip-bands": case_170_overlapping_clip_bands,
    "171-clip-with-too-many-pieces": case_171_clip_with_too_many_pieces,
    "172-scan-with-a-burnt-in-band": case_172_scan_with_a_burnt_in_band,
    "173-rotated-cover-over-blacked-image": case_173_rotated_cover_over_blacked_image,
    "174-black-image-over-photo": case_174_black_image_over_photo,
    "175-marker-stroke-over-photo": case_175_marker_stroke_over_photo,
    "176-marker-over-blacked-image": case_176_marker_over_blacked_image,
    "177-inline-separation-type4": case_177_inline_separation_type4,
    "178-band-over-part-of-a-run": case_178_band_over_part_of_a_run,
    "179-band-under-a-doubled-run": case_179_band_under_a_doubled_run,
    "180-anisotropic-stroke-over-photo": case_180_anisotropic_stroke_over_photo,
    "181-miter-join-over-photo": case_181_miter_join_over_photo,
    "182-butt-cap-stroke-correct": case_182_butt_cap_stroke_correct,
    "183-rotated-image-cover-over-strip": case_183_rotated_image_cover_over_strip,
    "184-clipped-cover-over-photo": case_184_clipped_cover_over_photo,
    "185-comb-image-under-cover": case_185_comb_image_under_cover,
    "186-band-just-short-of-the-glyph-box": case_186_band_just_short_of_the_glyph_box,
    "187-reversed-heading": case_187_reversed_heading,
    "188-black-on-scanned-band": case_188_black_on_scanned_band,
    "189-outlined-title": case_189_outlined_title,
    "190-stroke-only-white-text": case_190_stroke_only_white_text,
    "191-tr3-under-unreadable-cover": case_191_tr3_under_unreadable_cover,
    "192-form-inside-text-object": case_192_form_inside_text_object,
    "193-tagged-strings-past-the-cap": case_193_tagged_strings_past_the_cap,
    "194-earlier-revision-many-strings": case_194_earlier_revision_many_strings,
    "195-pattern-filled-text": case_195_pattern_filled_text,
    "196-curved-marker-correct": case_196_curved_marker_correct,
    "197-miter-join-repeated-vertex": case_197_miter_join_repeated_vertex,
    "198-round-cap-stroke-correct": case_198_round_cap_stroke_correct,
    "199-bevel-join-correct": case_199_bevel_join_correct,
    "200-closed-two-point-stroke": case_200_closed_two_point_stroke,
    "201-dashed-rule-over-image": case_201_dashed_rule_over_image,
    "202-anisotropic-diagonal-correct": case_202_anisotropic_diagonal_correct,
    "203-image-clipped-to-an-L": case_203_image_clipped_to_an_L,
    "204-band-behind-part-of-a-line": case_204_band_behind_part_of_a_line,
    "205-white-text-in-an-L-notch": case_205_white_text_in_an_L_notch,
    "206-black-text-in-an-L-notch": case_206_black_text_in_an_L_notch,
    "207-transparent-text-over-band": case_207_transparent_text_over_band,
    "208-text-clip-mode-7": case_208_text_clip_mode_7,
    "209-two-clips-in-sequence": case_209_two_clips_in_sequence,
    "210-sparse-stencil-over-photo": case_210_sparse_stencil_over_photo,
    "211-solid-stencil-over-photo": case_211_solid_stencil_over_photo,
    "212-stencil-band-over-ocr": case_212_stencil_band_over_ocr,
    "213-stencil-band-over-photo": case_213_stencil_band_over_photo,
    "214-text-outside-form-bbox": case_214_text_outside_form_bbox,
    "215-bowtie-clip": case_215_bowtie_clip,
    "216-solid-colour-stencil": case_216_solid_colour_stencil,
    "217-nearly-opaque-text": case_217_nearly_opaque_text,
    "218-triangular-hole": case_218_triangular_hole,
    "219-text-in-a-rotated-bars-corner": case_219_text_in_a_rotated_bars_corner,
    "220-text-in-a-clipped-covers-gap": case_220_text_in_a_clipped_covers_gap,
    "221-dotted-rule-over-photo": case_221_dotted_rule_over_photo,
    "222-odd-dash-array": case_222_odd_dash_array,
    "223-doubling-back-stroke": case_223_doubling_back_stroke,
    "224-faint-text-over-band": case_224_faint_text_over_band,
    "225-visible-half-opacity-text": case_225_visible_half_opacity_text,
    "226-ring-with-its-hole-filled": case_226_ring_with_its_hole_filled,
    "227-soft-masked-text": case_227_soft_masked_text,
    "228-rotated-form-bbox": case_228_rotated_form_bbox,
    "229-degenerate-clip-path": case_229_degenerate_clip_path,
    "230-cover-clipped-to-a-triangle": case_230_cover_clipped_to_a_triangle,
    "231-dashed-corner": case_231_dashed_corner,
    "232-pattern-stroked-outline": case_232_pattern_stroked_outline,
    "233-multiply-text": case_233_multiply_text,
    "234-anisotropic-curve": case_234_anisotropic_curve,
    "235-smask-then-none": case_235_smask_then_none,
    "236-smasked-cover": case_236_smasked_cover,
    "237-collinear-clip-window": case_237_collinear_clip_window,
    "238-pentagram-clip": case_238_pentagram_clip,
    "239-bowtie-fill-over-text": case_239_bowtie_fill_over_text,
    "240-uneven-bowtie-fill": case_240_uneven_bowtie_fill,
    "241-clipped-hole-cover": case_241_clipped_hole_cover,
    "242-smasked-image-cover": case_242_smasked_image_cover,
    "243-dash-gap-at-the-corner": case_243_dash_gap_at_the_corner,
    "244-dash-through-the-corner": case_244_dash_through_the_corner,
    "245-norotate-annot": case_245_norotate_annot,
    "246-ap-state-without-as": case_246_ap_state_without_as,
    "247-empty-stencil-over-text": case_247_empty_stencil_over_text,
    "248-terminal-dash-cap": case_248_terminal_dash_cap,
    "249-filled-and-stroked-faint": case_249_filled_and_stroked_faint,
    "250-explicit-closing-point": case_250_explicit_closing_point,
    "251-explicit-normal-blend": case_251_explicit_normal_blend,
    "252-closed-dash-terminal-cap": case_252_closed_dash_terminal_cap,
    "253-crossing-named-vertex": case_253_crossing_named_vertex,
    "254-diamond-hole": case_254_diamond_hole,
    "255-nested-island": case_255_nested_island,
    "256-shape-in-an-L-notch": case_256_shape_in_an_L_notch,
    "257-winding-island": case_257_winding_island,
    "258-white-multiply-over-band": case_258_white_multiply_over_band,
    "259-screen-image-over-text": case_259_screen_image_over_text,
    "260-closed-dash-wrap-cap": case_260_closed_dash_wrap_cap,
    "261-two-identical-diamonds": case_261_two_identical_diamonds,
    "262-overlapping-subpaths": case_262_overlapping_subpaths,
    "263-nearly-opaque-band": case_263_nearly_opaque_band,
    "264-white-text-on-screen-image": case_264_white_text_on_screen_image,
    "265-bar-across-a-U-notch": case_265_bar_across_a_U_notch,
    "266-terminal-dash-dot": case_266_terminal_dash_dot,
    "267-stroke-swallows-fill": case_267_stroke_swallows_fill,
    "268-half-transparent-image": case_268_half_transparent_image,
    "269-white-over-unknown-blend": case_269_white_over_unknown_blend,
    "270-far-control-curve": case_270_far_control_curve,
    "271-estimated-positions-clear": case_271_estimated_positions_clear,
    "272-font-without-widths": case_272_font_without_widths,
    "273-encrypt-direct-dictionary": case_273_encrypt_direct_dictionary,
    "274-encrypt-indirect-reference": case_274_encrypt_indirect_reference,
    "275-bar-with-named-crossings": case_275_bar_with_named_crossings,
    "276-zero-length-dot": case_276_zero_length_dot,
    "277-stretched-text-matrix": case_277_stretched_text_matrix,
    "278-half-image-over-black-band": case_278_half_image_over_black_band,
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

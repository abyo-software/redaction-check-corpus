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
  #90+    locally generated stand-ins for cases whose real article has to
          be produced by a specific application (#12 Acrobat, #13 the
          abyo editor). They are NOT substitutes in the manifest; they
          exist so this side can test for false positives today.

Usage: python3 generate.py [outdir]
"""

import os
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
        b"/IC[0 0 0]/C[0 0 0]/CA 1/F 4/Contents(masked)>>"
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
    info = pdf.add(
        b"<</Author(Yamada Taro)/Title(disclosure draft)"
        b"/Creator(\\\\fileserver\\share\\private\\2026\\draft-with-names.docx)"
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
    pdf = Pdf()
    img = photo(pdf)
    body = b"q\n451 0 0 638 72 102 cm\n/Im0 Do\nQ\n"
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

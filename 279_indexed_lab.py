#!/usr/bin/env python3
"""Build corpus case #279: a black-and-white Indexed Lab picture under a black box.

Editor fixture for spec 1.6 units.luminance (Codex v107 round 6): an Indexed
colour space's lookup byte b is min + b * (max - min) / 255 of its base
component's range - for Lab, L* runs 0..100 - never b / 255 for every base.
The picture is a checkerboard of palette index 0 (L* 0, black) and index 1
(L* byte 255, white), and a black box is drawn over the whole of it: the
picture under the box is intact, so A6 fails. Divided by 255, the white is
L* 1 - dark - the picture reads as blacked out, and the file passes.

Same style as generate.py (copied verbatim from the checker repository and
not edited here): standard library only, raw PDF bytes, deterministic. It
reuses generate.py's writer by loading it from this folder.

Expected verdicts live in manifest.json, never here.

Usage: python 279_indexed_lab.py [outdir]   (default ./files)
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = "279-indexed-lab-picture-under-box.pdf"
SIDE = 16


def _load_generator():
    # Loaded by path: `python -I` does not put the script's folder on sys.path.
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("redaction_corpus_generate", os.path.join(HERE, "generate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def case_279(g) -> bytes:
    pdf = g.Pdf()
    f = g.helvetica(pdf)
    samples = bytes(((x + y) % 2) for y in range(SIDE) for x in range(SIDE))
    # [/Indexed /Lab 1 lookup]: index 0 = L* 0, a* b* 128 (about 0); index 1 = L* 255 (100), a* b* 128
    img = pdf.stream(
        samples,
        f"/Type/XObject/Subtype/Image/Width {SIDE}/Height {SIDE}/BitsPerComponent 8"
        "/ColorSpace[/Indexed[/Lab<</WhitePoint[0.9505 1 1.089]>>]1<0080 80FF 8080>]",
    )
    body = (
        g.text_op(72, 760, "Exhibit, one photograph withheld")
        + b"q\n200 0 0 200 100 300 cm\n/Im0 Do\nQ\n"
        + g.black_rect(100, 300, 200, 200)
    )
    root, _ = g.page(pdf, body, font=f, xobjects={"Im0": img})
    return pdf.build(root, doc_id="ABYOCORPUS0279")


def main() -> int:
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "files")
    os.makedirs(outdir, exist_ok=True)
    data = case_279(_load_generator())
    with open(os.path.join(outdir, FILE), "wb") as fh:
        fh.write(data)
    print(f"  {FILE}  {len(data):>7} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build corpus case #280: a Type 3 d0 glyph that paints white, shown in black on paper.

Editor fixture for spec 1.6 type3Glyphs (Codex v107 round 6): a glyph whose
procedure begins with d0 sets its own colours and paints in them, never in
the colour in force where it is shown. Here the procedure paints a white
rectangle and the line is shown with a black fill: on white paper the glyphs
are white on white - invisible on the page, extractable - so A4 fails
(same-as-background). Read in the caller's black, the line is visible and the
file passes.

Same style as generate.py: standard library only, raw PDF bytes,
deterministic, reusing generate.py's writer.

Expected verdicts live in manifest.json, never here.

Usage: python 280_type3_d0.py [outdir]   (default ./files)
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILE = "280-type3-d0-white-glyphs.pdf"


def _load_generator():
    # Loaded by path: `python -I` does not put the script's folder on sys.path.
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("redaction_corpus_generate", os.path.join(HERE, "generate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def case_280(g) -> bytes:
    pdf = g.Pdf()
    # one glyph, /A: 160 x 22 in glyph space (identity font matrix, a font size of 1), painting itself white
    proc = pdf.stream(b"160 0 d0\n1 g\n0 0 160 22 re f\n")
    type3 = pdf.add(
        f"<</Type/Font/Subtype/Type3/FontBBox[0 0 160 22]/FontMatrix[1 0 0 1 0 0]"
        f"/CharProcs<</A {proc} 0 R>>/Encoding<</Type/Encoding/Differences[97/A]>>"
        f"/FirstChar 97/LastChar 97/Widths[160]>>".encode()
    )
    body = b"BT\n0 g\n/F1 1 Tf\n0.1 0 0 1 68 694 Tm\n(aaa) Tj\nET\n"
    root, _ = g.page(pdf, body, font=type3)
    return pdf.build(root, doc_id="ABYOCORPUS0280")


def main() -> int:
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "files")
    os.makedirs(outdir, exist_ok=True)
    data = case_280(_load_generator())
    with open(os.path.join(outdir, FILE), "wb") as fh:
        fh.write(data)
    print(f"  {FILE}  {len(data):>7} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

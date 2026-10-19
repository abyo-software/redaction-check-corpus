#!/usr/bin/env python3
"""Build corpus case #16: the same name redacted on page 1, left on page 5.

Editor-only (requirement 3.6, "black-out candidates"): the name was
properly removed from page 1 - the text is gone from the content stream
and a black bar is burnt in - but the same name still stands, visible
and uncovered, on page 5. Nothing is HIDDEN anywhere, so the checker
(appendix A) must pass this file: it does not judge completeness. The
editor's candidate detection is what should point at page 5.

Same style as generate.py (which is copied verbatim from the checker
repository and is not edited here): standard library only, raw PDF
bytes, deterministic. It reuses generate.py's writer by loading it from
this folder, so the two cannot drift apart in how a file is laid out.

Expected verdicts live in manifest.json, never here.

Usage: python 16_same_name.py [outdir]   (default ./files)
"""

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_COUNT = 5
NAME = "Yamada Taro"


def _load_generator():
    # Loaded by path: `python -I` does not put the script's folder on sys.path.
    # No __pycache__ next to the corpus.
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location("redaction_corpus_generate", os.path.join(HERE, "generate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def case_16_same_name_other_page(g) -> bytes:
    pdf = g.Pdf()
    f = g.helvetica(pdf)
    pages_num = pdf.reserve()

    bodies = []
    for n in range(1, PAGE_COUNT + 1):
        body = g.text_op(72, 760, f"Disclosure copy - page {n} of {PAGE_COUNT}")
        if n == 1:
            # Redacted properly: the label stays, the name is gone, a bar is burnt in.
            body += g.text_op(72, 700, "Applicant: ") + g.black_rect(150, 694, 100, 26)
        elif n == PAGE_COUNT:
            # The same name, missed: plain visible text, nothing over it.
            body += g.text_op(72, 700, f"Applicant: {NAME}")
        else:
            body += g.text_op(72, 700, "Ordinary body text that is meant to be read.")
        bodies.append(body)

    kids = []
    for body in bodies:
        page_num = pdf.reserve()
        contents = pdf.stream(body)
        pdf.put(
            page_num,
            (
                f"<</Type/Page/Parent {pages_num} 0 R"
                f"/MediaBox[0 0 {g.PAGE_W} {g.PAGE_H}]"
                f"/Resources<</Font<</F1 {f} 0 R>>>>"
                f"/Contents {contents} 0 R>>"
            ).encode(),
        )
        kids.append(page_num)

    pdf.put(
        pages_num,
        (
            "<</Type/Pages/Kids[" + " ".join(f"{k} 0 R" for k in kids) + f"]/Count {len(kids)}>>"
        ).encode(),
    )
    catalog = pdf.add(f"<</Type/Catalog/Pages {pages_num} 0 R>>".encode())
    return pdf.build(catalog, doc_id="ABYOCORPUS0016")


def main() -> int:
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "files")
    os.makedirs(outdir, exist_ok=True)
    data = case_16_same_name_other_page(_load_generator())
    path = os.path.join(outdir, "16-same-name-other-page.pdf")
    with open(path, "wb") as fh:
        fh.write(data)
    print(f"  16-same-name-other-page.pdf  {len(data):>7} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

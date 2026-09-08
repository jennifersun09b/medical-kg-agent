#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_pdf.py — Read a source PDF as text; optionally grep terms for number-checking.

Use to (a) read downloaded guideline/label originals, and (b) verify KB numbers
(incidence / price / dose / HR) against the primary source.

Usage:
    python3 extract_pdf.py file.pdf                     # print all text
    python3 extract_pdf.py file.pdf out.txt             # write text to file
    python3 extract_pdf.py file.pdf --find 3.1% ONJ 补钙  # print context windows for each term

Dependency: pdfplumber  (pip3 install pdfplumber)
"""
import sys, logging, warnings

def main():
    args = sys.argv[1:]
    if not args:
        sys.stderr.write("usage: python3 extract_pdf.py file.pdf [out.txt | --find TERM ...]\n"); sys.exit(1)
    try:
        import pdfplumber
    except ImportError:
        sys.stderr.write("ERROR: pip3 install pdfplumber\n"); sys.exit(1)
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    warnings.filterwarnings("ignore")

    path = args[0]
    try:
        with pdfplumber.open(path) as pdf:
            npages = len(pdf.pages)
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception as e:
        sys.stderr.write("ERROR opening %s: %s\n" % (path, e)); sys.exit(1)

    if len(text.strip()) < 40:
        sys.stderr.write("WARNING: almost no text extracted (%d pages) — PDF may be image-only; "
                         "OCR needed (see the pdf skill).\n" % npages)

    rest = args[1:]
    if rest and rest[0] == "--find":
        flat = " ".join(text.split())          # collapse to one line for stable windows
        for term in rest[1:]:
            print("### %s" % term)
            idx, cnt = 0, 0
            while cnt < 4:
                i = flat.find(term, idx)
                if i < 0:
                    break
                print("   …" + flat[max(0, i - 50):i + 95].strip() + "…")
                idx = i + len(term); cnt += 1
            if cnt == 0:
                print("   (未命中)")
            print()
    elif rest:
        with open(rest[0], "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote %d chars (%d pages) to %s" % (len(text), npages, rest[0]))
    else:
        print(text)

if __name__ == "__main__":
    main()

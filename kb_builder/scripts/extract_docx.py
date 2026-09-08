#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_docx.py — Dump a .docx as readable text + tables, in document order.

Use to read source material before extracting entities. Tables are rendered as
pipe-separated rows between ===TABLE=== / ===/TABLE=== markers.

Usage:
    python3 extract_docx.py file.docx            # print to stdout
    python3 extract_docx.py file.docx out.txt    # write to file

Dependency: python-docx  (pip3 install python-docx)
"""
import sys

def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: python3 extract_docx.py file.docx [out.txt]\n"); sys.exit(1)
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        sys.stderr.write("ERROR: pip3 install python-docx\n"); sys.exit(1)

    d = Document(sys.argv[1])
    out = []
    for child in d.element.body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, d)
            if p.text.strip():
                out.append(p.text)
        elif child.tag == qn("w:tbl"):
            tbl = Table(child, d)
            out.append("===TABLE===")
            for row in tbl.rows:
                out.append(" | ".join(c.text.strip().replace("\n", " ") for c in row.cells))
            out.append("===/TABLE===")
    text = "\n".join(out)
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote %d chars to %s" % (len(text), sys.argv[2]))
    else:
        print(text)

if __name__ == "__main__":
    main()

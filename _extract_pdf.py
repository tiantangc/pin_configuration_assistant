# -*- coding: utf-8 -*-
"""Extract text from MSPM0 PDFs to .txt files for analysis."""
import fitz
import os

base = r"F:\pin_configuration_assistant"
pdfs = [
    (r"【MSPM0G3507】开源硬件\天猛星MSPM0G3507开发板引脚图.pdf", "pinout.txt"),
    (r"【MSPM0G3507】开源硬件\立创·天猛星MSPM0G3507开发板原理图.pdf", "schematic.txt"),
    (r"【MSPM0G3507】官方资料\MSPM0G系列硬件手册.pdf", "hw_manual.txt"),
    (r"【MSPM0G3507】官方资料\mspm0g3507数据手册.pdf", "datasheet.txt"),
    (r"【MSPM0G3507】官方资料\mspm0g3507用户手册.pdf", "user_manual.txt"),
]

for rel, out in pdfs:
    path = os.path.join(base, rel)
    doc = fitz.open(path)
    parts = []
    for i, page in enumerate(doc):
        parts.append(f"\n===== PAGE {i+1} =====\n")
        parts.append(page.get_text())
    text = "".join(parts)
    outpath = os.path.join(base, "_" + out)
    with open(outpath, "w", encoding="utf-8") as f:
        f.write(text)
    print(out, doc.page_count, "pages ->", len(text), "chars ->", outpath)
    doc.close()

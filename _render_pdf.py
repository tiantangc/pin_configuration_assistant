# -*- coding: utf-8 -*-
"""Render MSPM0 board PDFs to PNG images for vision analysis."""
import fitz
import os

base = r"F:\pin_configuration_assistant"

jobs = [
    (r"【MSPM0G3507】开源硬件\天猛星MSPM0G3507开发板引脚图.pdf", "pinout", 3.0),
    (r"【MSPM0G3507】开源硬件\立创·天猛星MSPM0G3507开发板原理图.pdf", "schem", 3.0),
]

for rel, tag, zoom in jobs:
    path = os.path.join(base, rel)
    doc = fitz.open(path)
    for i, page in enumerate(doc):
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        out = os.path.join(base, f"_img_{tag}_p{i+1}.png")
        pix.save(out)
        print("saved", out, pix.width, "x", pix.height)
    doc.close()

# -*- coding: utf-8 -*-
"""Dump words with coordinates from pinout PDF, grouped into rows by y-position."""
import fitz
import os

base = r"F:\pin_configuration_assistant"
path = os.path.join(base, r"【MSPM0G3507】开源硬件\天猛星MSPM0G3507开发板引脚图.pdf")
doc = fitz.open(path)
page = doc[0]
words = page.get_text("words")  # x0, y0, x1, y1, word, block, line, word_no
# sort by y then x
words.sort(key=lambda w: (round(w[1], 0), w[0]))
# group into lines by y0 tolerance
lines = []
cur = []
last_y = None
for w in words:
    y = round(w[1], 0)
    if last_y is None or abs(y - last_y) <= 3:
        cur.append(w)
    else:
        lines.append(cur)
        cur = [w]
    last_y = y
if cur:
    lines.append(cur)

for ln in lines:
    ln.sort(key=lambda w: w[0])
    txt = " | ".join(f"{w[4]}" for w in ln)
    y0 = ln[0][1]
    x0 = ln[0][0]
    print(f"y={y0:6.1f} x={x0:6.1f}  {txt}")
doc.close()

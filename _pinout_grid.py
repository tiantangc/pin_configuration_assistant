# -*- coding: utf-8 -*-
"""Reconstruct the physical pin grid from the pinout PDF using word positions."""
import fitz, os, re

base = r"F:\pin_configuration_assistant"
path = os.path.join(base, r"【MSPM0G3507】开源硬件\天猛星MSPM0G3507开发板引脚图.pdf")
doc = fitz.open(path)
page = doc[0]
words = page.get_text("words")

# pin label patterns: A## / B## (port pins) plus PA##/PB## forms
pin_re = re.compile(r'^[AB]\d{2}$')
special = {'GND','3V3','5V','RST','BSL','ROSC','CHIP','AGND','VREF+','VREF-','DAC-OUT','CLK','DIO'}

# We want the pin-name labels only (single tokens).  Function labels contain '-C0', '-RX', 'SPI', 'UART' etc.
rows = []
for w in words:
    x0,y0,x1,y1,txt = w[0],w[1],w[2],w[3],w[4]
    t = txt.strip()
    if t in special or pin_re.match(t):
        rows.append((round(y0,1), round(x0,1), t))

rows.sort()
for y,x,t in rows:
    print(f"y={y:7.1f} x={x:7.1f}  {t}")
doc.close()

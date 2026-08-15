# -*- coding: utf-8 -*-
import fitz, os
base = r"F:\pin_configuration_assistant"
path = os.path.join(base, r"【MSPM0G3507】开源硬件\天猛星MSPM0G3507开发板引脚图.pdf")
doc = fitz.open(path)
page = doc[0]
mat = fitz.Matrix(5.0, 5.0)
pix = page.get_pixmap(matrix=mat)
out = os.path.join(base, "_img_pinout_hi.png")
pix.save(out)
print("saved", out, pix.width, "x", pix.height)
doc.close()

# -*- coding: utf-8 -*-
"""生成 assets/truedesk.ico 程序图标（64x64 蓝色圆角 + 白色 TD）。"""
import os
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets", "truedesk.ico")
os.makedirs(os.path.dirname(OUT), exist_ok=True)

size = 64
img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([2, 2, size - 2, size - 2], radius=14, fill=(31, 66, 122))
try:
    font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 30)
except Exception:
    font = ImageFont.load_default()
label = "TD"
bbox = d.textbbox((0, 0), label, font=font)
w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
d.text(((size - w) / 2 - bbox[0], (size - h) / 2 - bbox[1] - 2), label, font=font,
       fill=(255, 255, 255))
img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
print("saved:", OUT)

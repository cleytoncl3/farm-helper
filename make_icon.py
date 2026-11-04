from PIL import Image, ImageDraw, ImageFont
S = 256
im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(im)
d.rounded_rectangle((8, 8, S - 8, S - 8), radius=56, fill=(31, 157, 85, 255))
d.ellipse((60, 60, S - 60, S - 60), fill=(255, 255, 255, 40))
try:
    f = ImageFont.truetype("segoeuib.ttf", 150)
except Exception:
    f = ImageFont.load_default()
d.text((S / 2, S / 2 + 4), "R", font=f, fill=(255, 255, 255, 255), anchor="mm")
im.save("fh_icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

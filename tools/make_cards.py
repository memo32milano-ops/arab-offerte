"""Branded images for themed offer posts (our own artwork: Amazon product photos may only come from their API)."""
import json, os, sys, math
from PIL import Image, ImageDraw, ImageFont, ImageFilter
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
EMO = '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf'
TB = os.path.join(HERE, 'Tajawal-Black.ttf'); TX = os.path.join(HERE, 'Tajawal-ExtraBold.ttf')
W, H = 1280, 720
PAL = [((255, 94, 58), (190, 20, 60)), ((40, 120, 255), (90, 20, 200)), ((0, 170, 120), (0, 90, 140)),
       ((255, 170, 0), (230, 70, 0)), ((150, 60, 255), (230, 40, 140)), ((20, 30, 60), (60, 20, 90))]

def emoji(ch, size):
    f = ImageFont.truetype(EMO, 109)
    im = Image.new('RGBA', (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((80, 80), ch, font=f, embedded_color=True, anchor='mm')
    im = im.crop(im.getbbox()); r = size / max(im.size)
    return im.resize((int(im.width * r), int(im.height * r)), Image.LANCZOS)

def card(e, title, pct, out, idx, sub='أكبر خصومات أمازون إيطاليا'):
    c1, c2 = PAL[idx % len(PAL)]
    bg = Image.new('RGB', (W, H)); px = bg.load()
    for y in range(H):
        for x in range(0, W, 4):
            t = (x / W) * 0.6 + (y / H) * 0.4
            col = tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3))
            for k in range(4): px[x + k, y] = col
    im = bg.convert('RGBA'); d = ImageDraw.Draw(im)
    # soft circles
    for (cx, cy, r, a) in [(1100, 120, 260, 40), (180, 650, 220, 35), (650, 760, 300, 25)]:
        o = Image.new('RGBA', (W, H), (0, 0, 0, 0)); ImageDraw.Draw(o).ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, a))
        im.alpha_composite(o)
    # big emoji with glow (left)
    em = emoji(e, 330)
    glow = Image.new('RGBA', (W, H), (0, 0, 0, 0)); glow.alpha_composite(em, (110, (H - em.height) // 2))
    sh = glow.filter(ImageFilter.GaussianBlur(30)); im.alpha_composite(Image.eval(sh, lambda v: v))
    im.alpha_composite(em, (110, (H - em.height) // 2))
    d = ImageDraw.Draw(im)
    # discount badge
    bx, by, br = 1060, 170, 125
    d.ellipse((bx - br, by - br, bx + br, by + br), fill=(255, 221, 0), outline=(255, 255, 255), width=8)
    d.text((bx, by - 22), f'-{pct}%', font=ImageFont.truetype(TB, 86), fill=(20, 20, 30), anchor='mm')
    d.text((bx, by + 52), 'وأكتر', font=ImageFont.truetype(TB, 44), fill=(20, 20, 30), anchor='mm', direction='rtl')
    # title (right aligned)
    size = 108
    f = ImageFont.truetype(TB, size)
    while d.textlength(title, font=f, direction='rtl') > 700 and size > 60:
        size -= 6; f = ImageFont.truetype(TB, size)
    d.text((1210, 400), title, font=f, fill=(255, 255, 255), anchor='rm', direction='rtl', stroke_width=4, stroke_fill=(0, 0, 0, 90))
    d.text((1210, 500), sub, font=ImageFont.truetype(TX, 46), fill=(255, 255, 255, 235), anchor='rm', direction='rtl')
    # footer strip
    d.rounded_rectangle((560, 590, 1210, 668), 39, fill=(255, 255, 255))
    d.text((885, 629), 'عروض إيطاليا بالعربي 🔥'.replace(' 🔥', ''), font=ImageFont.truetype(TB, 44), fill=(200, 30, 50), anchor='mm', direction='rtl')
    # italian flag stripe
    for i, col in enumerate([(0, 146, 70), (255, 255, 255), (206, 43, 55)]):
        d.rectangle((i * W // 3, H - 12, (i + 1) * W // 3, H), fill=col)
    im.convert('RGB').save(out, quality=88)

if __name__ == '__main__':
    camps = json.load(open(os.path.join(ROOT, 'campaigns.json'), encoding='utf-8'))
    os.makedirs(os.path.join(ROOT, 'img'), exist_ok=True)
    for i, c in enumerate(camps):
        card(c['e'], c['t'], c.get('p', 50), os.path.join(ROOT, 'img', f"c{i:02d}.jpg"), i)
    card('⚡', 'عروض اليوم', 70, os.path.join(ROOT, 'img', 'hub.jpg'), 5, 'خصومات على كل حاجة في أمازون')
    print('ok', len(camps))

# receipt.py
from PIL import Image, ImageDraw, ImageFont
import os
from datetime import datetime

LOGO_PATH = "assets/logo.png"  # optional path to your logo
OUT_DIR = "receipts"
os.makedirs(OUT_DIR, exist_ok=True)

def get_font(size=22):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()

def generate_receipt_image(txid, date, from_account, to_account, amount, status):
    # if date is None or "now", use current timestamp
    if (not date) or (str(date).lower() == "now"):
        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    W, H = 800, 1000
    bg_color = (0, 0, 0)         # black background
    gold = (201, 161, 81)        # gold color
    white = (240, 240, 240)      # white text

    # create background
    im = Image.new("RGB", (W, H), color=bg_color)
    draw = ImageDraw.Draw(im)

    # add logo if exists
    if os.path.exists(LOGO_PATH):
        try:
            logo = Image.open(LOGO_PATH).convert("RGBA")
            logo.thumbnail((int(W * 0.4), int(W * 0.4)))  # scale logo
            lx = (W - logo.width) // 2
            im.paste(logo, (lx, 40), logo)
        except Exception:
            pass

    # bank name (unified)
    f_big = get_font(40)
    text = "SOLEN BANK"
    bbox = draw.textbbox((0, 0), text, font=f_big)  # bounding box
    w = bbox[2] - bbox[0]
    draw.text(((W - w) / 2, 300), text, font=f_big, fill=gold)

    # divider
    draw.line([(80, 360), (W - 80, 360)], fill=gold, width=2)

    # transaction details
    f_label = get_font(24)
    f_val = get_font(26)
    start_y = 400
    gap = 60
    lines = [
        ("Transaction ID:", txid),
        ("Date:", date),
        ("From Account:", from_account),
        ("To Account:", to_account),
        ("Amount:", f"{amount} Solen"),
        ("Status:", status),
    ]
    x_label = 100
    x_val = 350
    for i, (lab, val) in enumerate(lines):
        y = start_y + i * gap
        draw.text((x_label, y), lab, font=f_label, fill=white)
        color_val = gold if lab == "Status:" and str(val).lower() == "completed" else white
        draw.text((x_val, y), str(val), font=f_val, fill=color_val)

    # footer line
    draw.line([(80, H - 120), (W - 80, H - 120)], fill=gold, width=2)

    # save file
    out_path = os.path.join(OUT_DIR, f"receipt_{txid}.png")
    im.save(out_path)
    return out_path

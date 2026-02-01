from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


TEHRAN_TZ = ZoneInfo("Asia/Tehran")
CURRENCY_UNIT = "SOLEN"


def _make_numeric_receipt_no() -> str:
    """
    Temporary numeric receipt number (timestamp-based).
    Later we can replace with DB-backed incremental counter.
    """
    ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return str(ms)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    No external font file required.
    Pillow usually ships with DejaVu fonts; fallback to default if unavailable.
    """
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()


def generate_receipt(
    sender_account: str,
    receiver_account: str,
    amount: int,
    status: str,
    description: str | None = None,
    receipt_no: str | None = None,
) -> tuple[str, Image.Image]:
    """
    Generates a receipt image and returns (receipt_no, image)
    - receipt_no is numeric (string digits).
    - Timezone is Tehran (live).
    - Amount includes currency unit (SOLEN).
    - No dependency on assets/font.ttf or pytz.
    """
    receipt_no = receipt_no or _make_numeric_receipt_no()
    now = datetime.now(TEHRAN_TZ).strftime("%Y-%m-%d %H:%M:%S")

    width, height = 900, 540
    image = Image.new("RGB", (width, height), "#0f172a")
    draw = ImageDraw.Draw(image)

    title_font = _load_font(40)
    text_font = _load_font(26)
    small_font = _load_font(20)

    # Title
    draw.text(
        (width // 2, 30),
        "ECLIS BANKING SYSTEM",
        font=title_font,
        fill="#38bdf8",
        anchor="mm",
    )

    y = 110
    line_gap = 42

    fields = [
        ("Receipt No", receipt_no),
        ("Time (Tehran)", now),
        ("Sender Account", sender_account),
        ("Receiver Account", receiver_account),
        ("Amount", f"{amount:,} {CURRENCY_UNIT}"),
        ("Status", status),
    ]

    for label, value in fields:
        draw.text((60, y), f"{label}:", font=text_font, fill="#e5e7eb")
        draw.text((360, y), str(value), font=text_font, fill="#f8fafc")
        y += line_gap

    if description:
        y += 10
        draw.text((60, y), "Description:", font=text_font, fill="#e5e7eb")
        y += 32

        # simple wrap to avoid overflowing
        max_chars = 70
        desc = str(description).strip()
        lines = [desc[i : i + max_chars] for i in range(0, len(desc), max_chars)]
        for line in lines[:4]:
            draw.text((60, y), line, font=small_font, fill="#cbd5f5")
            y += 28

    # Footer
    draw.line((40, height - 70, width - 40, height - 70), fill="#334155", width=2)
    draw.text(
        (width // 2, height - 40),
        "This receipt is system-generated and non-editable",
        font=small_font,
        fill="#94a3b8",
        anchor="mm",
    )

    return receipt_no, image

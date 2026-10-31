"""Generate the deterministic scanned-PDF fixtures used by parse OCR tests.

Run from the repository root::

    .venv/bin/python scripts/generate_parse_ocr_fixtures.py

The committed PDFs are authoritative because Pillow's bundled default font and
PDF encoder can change between versions; rerun only when intentionally
refreshing the fixtures. ``scanned-readable.pdf`` renders known phrases as an
image-only page. ``scanned-degraded.pdf`` applies deterministic blur and
salt-and-pepper noise to the same page so a deliberately degraded OCR outcome
can be exercised. Neither fixture contains an extractable text layer.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "anydoc"
LINES = (
    "ASCII format for Network Interchange",
    "Standard 7-bit ASCII embedded in an 8-bit byte.",
)
CANVAS = (1600, 440)
DPI = 200.0


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _render() -> Image.Image:
    image = Image.new("RGB", CANVAS, "white")
    draw = ImageDraw.Draw(image)
    font = _font(64)
    draw.text((70, 130), LINES[0], font=font, fill="black")
    draw.text((70, 230), LINES[1], font=_font(40), fill="black")
    return image


def _degrade(image: Image.Image) -> Image.Image:
    degraded = image.filter(ImageFilter.GaussianBlur(radius=2.4))
    pixels = degraded.load()
    width, height = degraded.size
    step = 5
    for y in range(0, height, step):
        for x in range(0, width, step):
            dark = ((x // step) + (y // step)) % 2 == 0
            pixels[x, y] = (0, 0, 0) if dark else (255, 255, 255)
    return degraded


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    page = _render()
    page.save(FIXTURES / "scanned-readable.pdf", "PDF", resolution=DPI)
    _degrade(page).save(FIXTURES / "scanned-degraded.pdf", "PDF", resolution=DPI)
    print("wrote", FIXTURES / "scanned-readable.pdf")
    print("wrote", FIXTURES / "scanned-degraded.pdf")


if __name__ == "__main__":
    main()

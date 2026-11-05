from PIL import Image, ImageDraw


def create_printer_icon(output_path: str = "app_icon.ico"):
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded dark blue background badge
    draw.rounded_rectangle((12, 12, 244, 244), radius=48, fill=(30, 58, 138, 255))

    # Top paper tray (white sheet sticking out)
    draw.rounded_rectangle((68, 36, 188, 105), radius=8, fill=(241, 245, 249, 255))

    # Printer main body
    draw.rounded_rectangle((36, 88, 220, 178), radius=20, fill=(255, 255, 255, 255))
    draw.rounded_rectangle((48, 104, 208, 132), radius=8, fill=(15, 23, 42, 255))

    # Status green LED dot on printer
    draw.ellipse((180, 112, 194, 126), fill=(34, 197, 94, 255))

    # Output paper coming out at bottom with invoice lines
    draw.rounded_rectangle((64, 150, 192, 224), radius=8, fill=(248, 250, 252, 255), outline=(203, 213, 225, 255), width=3)
    draw.line((82, 170, 174, 170), fill=(37, 99, 235, 255), width=6)
    draw.line((82, 188, 154, 188), fill=(100, 116, 139, 255), width=5)
    draw.line((82, 204, 168, 204), fill=(22, 163, 74, 255), width=6)

    img.save(output_path, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"Icon saved: {output_path}")


if __name__ == "__main__":
    create_printer_icon()


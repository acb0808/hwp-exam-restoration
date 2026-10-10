"""Combine several small inspection images into one labelled sheet.

Each image is a separate model turn and a fixed image-token cost, so packing
requested crops into one sheet saves turns. Crops keep their native pixels:
nothing is scaled down, because legibility is the reason they were requested.
"""
from pathlib import Path

from PIL import Image, ImageDraw

LABEL_H = 16
PAD = 6
MAX_WIDTH = 1400


def label_font(*texts):
    """A font that can print the labels, or None for Pillow's built-in ASCII font."""
    if all(str(t).isascii() for t in texts):
        return None
    from PIL import ImageFont
    for name in ('malgun.ttf', 'NanumGothic.ttf', 'AppleGothic.ttf', 'NotoSansCJK-Regular.ttc'):
        try:
            return ImageFont.truetype(name, 13)
        except OSError:
            continue
    return None


def montage(items, output, *, max_width=MAX_WIDTH):
    """items: [(label, image_path)]. Returns the output path; tiles are shelf-packed."""
    if not items:
        raise ValueError('montage_requires_images')
    tiles = []
    for label, path in items:
        with Image.open(path) as im:
            tiles.append((str(label), im.convert('RGB')))
    limit = max(max_width, max(t[1].width for t in tiles) + 2 * PAD)
    rows, row, used = [], [], PAD
    for tile in tiles:
        if row and used + tile[1].width + PAD > limit:
            rows.append(row)
            row, used = [], PAD
        row.append(tile)
        used += tile[1].width + PAD
    rows.append(row)
    width = max(PAD + sum(t[1].width + PAD for t in r) for r in rows)
    height = PAD + sum(LABEL_H + max(t[1].height for t in r) + PAD for r in rows)
    sheet = Image.new('RGB', (width, height), '#f2f2f2')
    draw = ImageDraw.Draw(sheet)
    y = PAD
    for r in rows:
        x = PAD
        for label, im in r:
            draw.text((x, y), label, fill='#c00000')
            sheet.paste(im, (x, y + LABEL_H))
            draw.rectangle((x - 1, y + LABEL_H - 1, x + im.width, y + LABEL_H + im.height), outline='#888888')
            x += im.width + PAD
        y += LABEL_H + max(t[1].height for t in r) + PAD
    output = Path(output)
    sheet.save(output)
    return str(output)


def side_by_side(source_crop, render, output, *, label_left='source', label_right='render', max_height=900, min_height=0, white=False):
    """Source crop and rendered figure at the same height, for a single-view comparison.
    min_height: a small crop is enlarged to this height with its render, as far as the pair stays within MAX_WIDTH.
    A crop cut close around a small figure would otherwise show its render at 250 pixels, marks and all.
    white: a plain white sheet for print (검수 노트) instead of the grey working sheet."""
    font = label_font(label_left, label_right)
    if font is None and not (str(label_left).isascii() and str(label_right).isascii()):
        label_left, label_right = 'source', 'output'
    with Image.open(source_crop) as a, Image.open(render) as b:
        a, b = a.convert('RGB'), b.convert('RGB')
        target = min(max_height, max(a.height, 1))
        if target < min_height:
            fits = int((MAX_WIDTH - 3 * PAD) / (a.width / a.height + b.width / b.height))
            target = max(target, min(min_height, max_height, fits))
        if a.height != target:
            a = a.resize((max(1, round(a.width * target / a.height)), target), Image.LANCZOS)
        wide = max(1, round(b.width * target / b.height))
        # A render much wider than tall (a table of one row) is shown no wider than its source crop or the room left.
        room = max(a.width, MAX_WIDTH - a.width - 3 * PAD)
        b = b.resize((room, max(1, round(b.height * room / b.width))) if wide > room else (wide, target), Image.LANCZOS)
        sheet = Image.new('RGB', (a.width + b.width + 3 * PAD, max(target, b.height) + LABEL_H + 2 * PAD), 'white' if white else '#f2f2f2')
        draw = ImageDraw.Draw(sheet)
        ink = '#404040' if white else '#c00000'
        draw.text((PAD, PAD - 2 if font else PAD), label_left, fill=ink, font=font)
        draw.text((a.width + 2 * PAD, PAD - 2 if font else PAD), label_right, fill=ink, font=font)
        sheet.paste(a, (PAD, PAD + LABEL_H))
        sheet.paste(b, (a.width + 2 * PAD, PAD + LABEL_H))
    output = Path(output)
    sheet.save(output)
    return str(output)

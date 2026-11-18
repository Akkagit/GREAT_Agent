"""Bounded formula strip renderer. Executed in a disposable credential-free child."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

MAX_PNG_BYTES = 2 * 1024 * 1024


def render_formula_crop(source: Path, page_index: int, bbox, output: Path):
    import pdfplumber
    import pypdfium2 as pdfium

    if type(page_index) is not int or not 0 <= page_index < 300:
        raise ValueError('invalid page')
    if not isinstance(bbox, (tuple, list)) or len(bbox) != 4 or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox):
        raise ValueError('invalid crop')
    x0, y0, x1, y1 = bbox
    # Only geometry qualified against PDFium is supported. Never guess for a
    # rotated, shifted, cropped or scaled source page.
    with pdfplumber.open(source) as pdf:
        if page_index >= len(pdf.pages):
            raise ValueError('unknown page')
        page = pdf.pages[page_index]
        media = tuple(float(v) for v in page.mediabox)
        crop = tuple(float(v) for v in (page.cropbox or page.mediabox))
        unit = page.page_obj.attrs.get('UserUnit', 1)
        if page.rotation != 0 or media[:2] != (0., 0.) or crop != media or unit != 1:
            raise ValueError('unqualified page geometry')
        width, height = page.width, page.height
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height
                and x1-x0 <= width/2 + .01 and y1-y0 <= 150):
            raise ValueError('crop exceeds bounds')
    with pdfium.PdfDocument(str(source)) as pdf:
        page = pdf[page_index]
        try:
            pw, ph = page.get_size()
            if abs(pw-width) > .01 or abs(ph-height) > .01:
                raise ValueError('page geometry disagrees')
            scale = min(3.0, 1998 / max(x1-x0, y1-y0))
            bitmap = page.render(scale=scale, crop=(x0, height-y1, width-x1, y0))
            try:
                image = bitmap.to_pil()
                try:
                    if max(image.size) > 2000:
                        raise ValueError('render too large')
                    image.save(output, format='PNG')
                finally:
                    image.close()
            finally:
                bitmap.close()
        finally:
            page.close()
    if output.stat().st_size > MAX_PNG_BYTES:
        output.unlink(missing_ok=True)
        raise ValueError('image too large')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--page', required=True, type=int)
    parser.add_argument('--bbox', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        render_formula_crop(Path(args.source), args.page, json.loads(args.bbox), Path(args.output))
    except Exception:
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()

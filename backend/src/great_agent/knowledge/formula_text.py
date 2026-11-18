"""Transcribe a narrow subset of visible, numbered equations in digital PDFs."""
import argparse
import json
import re
from pathlib import Path

import pdfplumber
from pdfminer.pdftypes import resolve1
from pdfminer.pdfinterp import PDFContentParser
from pdfminer.psparser import PSEOF, PSKeyword, keyword_name

from great_agent.knowledge.formula_render_process import render_formula_crop

MODEL = 'local_pdf_text_v1'
_TEXT_FONTS = {'Helvetica', 'Helvetica-Oblique', 'Helvetica-Bold', 'Helvetica-BoldOblique',
               'Times-Roman', 'Times-Italic', 'Times-Bold', 'Times-BoldItalic',
               'Courier', 'Courier-Oblique', 'Courier-Bold', 'Courier-BoldOblique', 'Symbol'}
_SYMBOLS = dict(zip('αβγδεζηθικλμνξπρστφχψωΓΔΘΛΞΠΣΦΨΩ',
                    ('alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi pi rho sigma '
                     'tau phi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Phi Psi Omega').split()))
_OPERATORS = {'−': '-', '×': r'\times ', '·': r'\cdot ', '≤': r'\le ', '≥': r'\ge '}


class Unsupported(ValueError):
    pass


def _symbol(char):
    if len(char) != 1:
        raise Unsupported()
    if char in _SYMBOLS:
        return '\\' + _SYMBOLS[char] + ' ', 'atom'
    if char in _OPERATORS:
        return _OPERATORS[char], 'relation' if char in '≤≥' else 'operator'
    if re.fullmatch(r'[A-Za-z0-9]', char):
        return char, 'atom'
    if char in '=<>':
        return char, 'relation'
    if char in '+-/':
        return char, 'operator'
    if char in '()':
        return char, char
    if char == '.':
        return '.', 'decimal'
    raise Unsupported()


def _check_stream(page):
    # pdfminer exposes text positions even for invisible or clipped text.
    # Only use pages with uncomplicated painting state and visible fill text.
    # The PDF text mapping may disagree with what the font actually paints.
    # Limit this extractor to standard, unembedded Type 1 fonts. Symbol needs
    # explicit glyph names; PDFMiner's default encoding can decode it as Latin.
    for resource in resolve1(page.page_obj.resources.get('Font', {})).values():
        font = resolve1(resource)
        base = getattr(font.get('BaseFont'), 'name', None)
        if (base not in _TEXT_FONTS or getattr(font.get('Subtype'), 'name', None) != 'Type1'
                or 'FontDescriptor' in font or 'ToUnicode' in font):
            raise Unsupported()
    parser = PDFContentParser(page.page_obj.contents)
    previous = None
    while True:
        try:
            _, token = parser.nextobject()
        except PSEOF:
            return
        if isinstance(token, PSKeyword):
            op = keyword_name(token)
            if op in {'W', 'W*', 'gs', 'Do', 'BI', 'sh', 'BDC', 'Ts'} or (op == 'Tr' and previous != 0):
                raise Unsupported()
        previous = token


def _single_atom(chars):
    if len(chars) != 1:
        raise Unsupported()
    latex, kind = _symbol(chars[0]['text'])
    if kind != 'atom':
        raise Unsupported()
    return latex.rstrip()


def _read_row(chars, lines, label):
    size = label['size']
    baseline = label['matrix'][5]
    body = []
    for line in lines:
        if (len(lines) != 1 or line['object_type'] != 'line' or line['height'] > .1
                or not line.get('stroke') or line.get('linewidth', 1) > size*.16
                or (line.get('dash') and line['dash'][0])
                or line.get('stroking_color') not in (None, 0, (0,), (0,0,0), (0,0,0,1))
                or not 0 < line['width'] <= 3 * size or abs(line['y0']-baseline) > .45 * size):
            raise Unsupported()
        stack = [c for c in chars if c['x0'] >= line['x0'] and c['x1'] <= line['x1']]
        above = [c for c in stack if 0 <= c['y0']-line['y0'] <= .5*size]
        below = [c for c in stack if 0 <= line['y0']-c['y1'] <= .5*size]
        if len(above) + len(below) != len(stack) or any(
                abs((c['x0']+c['x1']-line['x0']-line['x1'])/2) > size*.22 for c in stack):
            raise Unsupported()
        body.append((line['x0'], line['x1'], r'\frac{' + _single_atom(above) + '}{' + _single_atom(below) + '}', 'atom'))
        chars = [c for c in chars if c not in stack]
    for c in chars:
        latex, kind = _symbol(c['text'])
        dy = c['matrix'][5] - baseline
        if abs(dy) <= .12*size and .85*size <= c['size'] <= 1.15*size:
            pass
        elif kind == 'atom' and .48*size <= c['size'] <= .82*size and .2*size <= abs(dy) <= .65*size:
            latex = ('^{' if dy > 0 else '_{') + latex.rstrip() + '}'
            kind = 'script'
        else:
            raise Unsupported()
        body.append((c['x0'], c['x1'], latex, kind))

    out, previous, end = [], None, None
    depth = relations = 0
    body.sort()
    for i, (start, right, latex, kind) in enumerate(body):
        if end is not None and (start < end-size*.07 or start-end > size*2):
            raise Unsupported()
        if kind == 'script' and (previous not in {'atom', ')'} or start-end > size*.35):
            raise Unsupported()
        if out and out[-1].isdigit() and latex.isdigit() and abs(start-end) > size*.1:
            raise Unsupported()
        if kind == 'decimal':
            if (not out or not out[-1].isdigit() or i+1 == len(body)
                    or not body[i+1][2].isdigit() or start-end > size*.12
                    or body[i+1][0]-right > size*.12):
                raise Unsupported()
        if previous in {None, '(', 'operator', 'relation'} and kind not in {'atom', '('}:
            if latex != '-' or previous == 'operator':
                raise Unsupported()
        if kind == 'atom' and previous == 'atom' and not (out[-1].isdigit() or latex.isdigit()):
            # Adjacent letters may spell a function or ordinary prose.
            raise Unsupported()
        if kind == 'relation':
            if depth or previous not in {'atom', 'script', ')'}:
                raise Unsupported()
            relations += 1
        depth += (kind == '(') - (kind == ')')
        if depth < 0:
            raise Unsupported()
        out.append(latex)
        previous, end = kind, right
    if (depth or relations != 1 or previous not in {'atom', 'script', ')'}
            or re.search(r'\.\d+\.', ''.join(out))):
        raise Unsupported()
    return re.sub(r'\\([A-Za-z]+) (?=[^A-Za-z]|$)', r'\\\1', ''.join(out)).rstrip()


def extract_formula(path, page_index, bbox, number):
    """Return None for unsupported geometry, layout, fonts or math syntax."""
    if not re.fullmatch(r'[1-9][0-9]{0,2}', number) or not 0 <= page_index < 300:
        return None
    with pdfplumber.open(path, pages=[page_index+1]) as pdf:
        if not pdf.pages:
            return None
        page = pdf.pages[0]
        x0, top, x1, bottom = bbox
        if (page.rotation or tuple(page.mediabox[:2]) != (0, 0) or page.cropbox != page.mediabox
                or page.page_obj.attrs.get('UserUnit', 1) != 1 or not 0 <= x0 < x1 <= page.width
                or not 0 <= top < bottom <= page.height or x1-x0 > page.width/2+.01 or bottom-top > 150):
            return None
        try:
            _check_stream(page)
            if any(a['x1'] >= x0 and a['x0'] <= x1 and a['bottom'] >= top and a['top'] <= bottom
                   for a in page.annots):
                return None
            strip = page.within_bbox(tuple(bbox))
            tags = strip.search('('+number+')', regex=False) or []
            if len(tags) != 1:
                return None
            tag = tags[0]
            label = tag['chars'][0]
            size = label['size']
            # Use the entire strip. Extra prose or a second row makes it ambiguous.
            # Also inspect the immediate margins to detect clipped continuations.
            chars = []
            for c in page.chars:
                if c['top'] > bottom+size*.5 or c['bottom'] < top-size*.5 or c['text'] == ' ':
                    continue
                if c['x1'] < x0-2*size or c['x0'] > x1+2*size:
                    continue
                a,b,d,e,*_ = c['matrix']
                if (not c['upright'] or abs(b)+abs(d) > .01 or a <= 0 or e <= 0 or
                        c.get('non_stroking_color') not in (None, 0, (0,), (0,0,0), (0,0,0,1))):
                    raise Unsupported()
                if c['fontname'] == 'Symbol' and c['text'] not in _SYMBOLS and c['text'] not in _OPERATORS:
                    raise Unsupported()
                if c in tag['chars']:
                    if c['size'] != size or abs(c['matrix'][5]-label['matrix'][5]) > size*.1:
                        raise Unsupported()
                    continue
                if 'Bold' in c['fontname']:
                    raise Unsupported()
                if not (x0+1 <= c['x0'] < c['x1'] < tag['x0']-size and top+1 < c['top'] < c['bottom'] < bottom-1):
                    raise Unsupported()
                chars.append(c)
            if not 1 <= len(chars) <= 120:
                return None
            # Preserve distinctions such as roman and italic variants of x.
            variable_fonts = {}
            for c in chars:
                if c['text'].isalpha() and variable_fonts.setdefault(c['text'], c['fontname']) != c['fontname']:
                    return None
            lines = [obj for kind, objs in page.objects.items() if kind != 'char' for obj in objs
                     if obj['x1'] >= x0 and obj['x0'] <= x1 and obj['bottom'] >= top and obj['top'] <= bottom]
            return _read_row(chars, lines, label)
        except Unsupported:
            return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--page', required=True, type=int)
    parser.add_argument('--bbox', required=True, type=json.loads)
    parser.add_argument('--number', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    latex = extract_formula(args.source, args.page, args.bbox, args.number)
    if latex:
        render_formula_crop(args.source, args.page, args.bbox, args.output)
        args.output.with_suffix('.tex').write_text(latex, encoding='utf-8')


if __name__ == '__main__':
    main()

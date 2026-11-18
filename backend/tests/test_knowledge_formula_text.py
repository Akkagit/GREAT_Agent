"""Real text- and vector-layer PDFs with independently positioned mathematical glyphs."""
import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject


def digital_pdf(path, glyph_runs, graphics=b'', *, number='7', wrong_mapping=False, hide_label=False,
                annotation=False, bold=False):
    writer = PdfWriter()
    page = writer.add_blank_page(600, 800)
    fonts = DictionaryObject()
    for alias, font in [('F1', 'Helvetica'), ('F2', 'Helvetica-Bold' if bold else 'Symbol')]:
        definition = DictionaryObject({
            NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/'+font)})
        if wrong_mapping and font == 'Helvetica':
            cmap = DecodedStreamObject()
            cmap.set_data(b'begincmap 1 beginbfchar <33> <0038> endbfchar endcmap')
            definition[NameObject('/ToUnicode')] = writer._add_object(cmap)
        if font == 'Symbol':
            definition[NameObject('/Encoding')] = DictionaryObject({
                NameObject('/Type'): NameObject('/Encoding'), NameObject('/Differences'):
                    ArrayObject([NumberObject(97), NameObject('/alpha')])})
            definition[NameObject('/FirstChar')] = NumberObject(97)
            definition[NameObject('/LastChar')] = NumberObject(97)
            definition[NameObject('/Widths')] = ArrayObject([NumberObject(631)])
        fonts[NameObject('/'+alias)] = writer._add_object(definition)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): fonts})
    runs = list(glyph_runs)
    if number:
        runs.append(('F1', 10, 575, 600, '('+number+')'))
    data = []
    for font, size, x, y, chars in runs:
        if hide_label and x == 575:
            data.append(b'1 g')
        data.append(f'BT /{font} {size} Tf {x} {y} Td <{chars.encode("ascii").hex()}> Tj ET'.encode())
    stream = DecodedStreamObject()
    stream.set_data(b'\n'.join(data + [graphics]))
    page[NameObject('/Contents')] = writer._add_object(stream)
    if annotation:
        from pypdf.annotations import Rectangle
        writer.add_annotation(0, Rectangle(rect=(340,596,358,612), interior_color='ffffff'))
    writer.write(path)
    return path


def line(value):
    return ('F1', 10, 340, 600, value)


@pytest.mark.parametrize('runs,graphics,latex', [
    ([line('y = (2x+3)/5')], b'', 'y=(2x+3)/5'),
    ([line('v=2.75t'), ('F1', 10, 40, 600, 'A sentence in the separate left column.')],
     b'', 'v=2.75t'),
    ([line('x=2..4')], b'', None),
    ([line('x=2.4.9')], b'', None),
    ([line('x=1  2')], b'', None),
    ([('F2', 10, 340, 600, 'a'), ('F1', 10, 349, 600, '=3')], b'', r'\alpha=3'),
    ([line('x'), ('F1', 7, 345, 597, 'i'), ('F1', 10, 352, 600, '= t'),
      ('F1', 7, 364, 604, '2')], b'', 'x_{i}=t^{2}'),
    ([line('f='), ('F1', 8, 360, 607, 'a'), ('F1', 8, 360, 593, 'b')],
     b'0.5 w 358 602 m 366 602 l S', r'f=\frac{a}{b}'),
    ([line('x = y?')], b'', None),
    ([line('x = y'), ('F1', 10, 348, 614, 'a+b')], b'', None),
    ([line('x = y'), ('F1', 10, 280, 600, 't +')], b'', None),
    ([line('x = y')], b'345 612 m 376 612 l S', None),
    ([line('f='), ('F1', 8, 360, 648, 'a'), ('F1', 8, 360, 593, 'b')],
     b'0.5 w 358 602 m 366 602 l S', None),
    ([line('f='), ('F1', 8, 360, 607, 'a'), ('F1', 8, 360, 593, 'b')],
     b'[1 1] 0 d 0.5 w 358 602 m 366 602 l S', None),
    ([line('x=3')], b'BT /F1 10 Tf 3 Tr 375 600 Td (+4) Tj ET', None),
    ([line('x = y +')], b'', None),
    ([line('x = y'), ('F1', 10, 358, 600, 'y')], b'', None),
    ([], b'340 600 50 12 re f', None),
])
def test_local_text_transcribes_only_complete_supported_geometry(tmp_path, runs, graphics, latex):
    from great_agent.knowledge.formula_text import extract_formula
    path = digital_pdf(tmp_path/'eq.pdf', runs, graphics)
    assert extract_formula(path, 0, [300, 127, 600, 220], '7') == latex


@pytest.mark.parametrize('options', [{'hide_label':True}, {'wrong_mapping':True}, {'annotation':True}])
def test_unverified_font_map_or_invisible_label_is_omitted(tmp_path, options):
    from great_agent.knowledge.formula_text import extract_formula
    path = digital_pdf(tmp_path/'bad.pdf', [line('x=3')], **options)
    assert extract_formula(path, 0, [300, 127, 600, 220], '7') is None


def test_bold_variable_does_not_collapse_to_scalar(tmp_path):
    from great_agent.knowledge.formula_text import extract_formula
    path = digital_pdf(tmp_path/'bold.pdf',
                       [('F2', 10, 340, 600, 'x'), ('F1', 10, 350, 600, '=x')], bold=True)
    assert extract_formula(path, 0, [300, 127, 600, 220], '7') is None

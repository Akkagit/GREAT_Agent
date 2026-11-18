from pathlib import Path
from test_knowledge_parsing import make_pdf
from great_agent.knowledge.parsers import _parse_pdf

def test_parser_persists_scoped_formula_regions_with_profile_version(tmp_path):
    path=tmp_path/'equation.pdf'
    make_pdf(path,[[(40,730,'A scientific method and its equation below.'),
        (330,600,'x = y + z'),(575,600,'(7)'),(330,550,'See (7) for the detailed definition.')]])
    parsed=_parse_pdf(path,max_pages=1)
    regions=parsed.metadata['equation_regions']
    assert any(r['equation_number']=='7' and r['page_index']==0 for r in regions)
    assert len([r for r in regions if r['equation_number']=='7'])==1
    assert parsed.parser.config['equation_region_version']=='numbered-formula-region-1'

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


def make_pdf(path: Path, pages: list[list[tuple[float, float, str]]], *, grid=False):
    """Small real PDF fixture; coordinates supplied in PDF bottom-left points."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    page_ids = []
    for lines in pages:
        page_id = len(objects) + 1
        page_ids.append(page_id)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode())
        commands = []
        for x, y, value in lines:
            value = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"BT /F1 10 Tf {x} {y} Td ({value}) Tj ET")
        if grid:
            for x in (40, 150, 260):
                commands.append(f"{x} 600 m {x} 660 l S")
            for y in (600, 620, 640, 660):
                commands.append(f"40 {y} m 260 {y} l S")
        stream = "\n".join(commands).encode("ascii")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    kids = " ".join(f"{value} 0 R" for value in page_ids)
    objects[1] = f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode()
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(data)
    return path


def test_storage_content_addressing_and_duplicate_names(tmp_path):
    from great_agent.knowledge.storage import KnowledgeStorage

    storage = KnowledgeStorage(tmp_path / "knowledge")
    first = storage.store_bytes(b"A complete paragraph.", "paper.md")
    same = storage.store_bytes(b"A complete paragraph.", "another.md")
    changed = storage.store_bytes(b"Different content.", "paper.md")
    assert first["sha256"] == hashlib.sha256(b"A complete paragraph.").hexdigest()
    assert first["original_relpath"] == same["original_relpath"]
    assert first["original_relpath"] != changed["original_relpath"]
    assert first["media_type"] == "text/markdown"
    assert storage.original_path(first["original_relpath"]).read_bytes() == b"A complete paragraph."


def test_storage_streams_file_and_rejects_over_limit(tmp_path, monkeypatch):
    from great_agent.knowledge.storage import KnowledgeStorage

    storage = KnowledgeStorage(tmp_path / "knowledge", max_upload_bytes=16)
    upload = tmp_path / "upload.part"
    upload.write_bytes(b"first paragraph")
    monkeypatch.setattr(Path, "read_bytes", lambda self: pytest.fail("store_file must stream"))
    stored = storage.store_file(upload, "source.txt")
    assert stored["size_bytes"] == 15
    upload.write_bytes(b"x" * 17)
    with pytest.raises(ValueError, match="limit|large"):
        storage.store_file(upload, "source.txt")
    with pytest.raises(ValueError, match="empty"):
        storage.store_bytes(b"", "empty.txt")


@pytest.mark.parametrize("value", ["../escape.txt", "C:\\secret.txt", "a/b.txt", "a\\b.txt", "bad.exe", "a\x00.txt"])
def test_storage_rejects_untrusted_names(tmp_path, value):
    from great_agent.knowledge.storage import KnowledgeStorage

    with pytest.raises(ValueError):
        KnowledgeStorage(tmp_path / "knowledge").store_bytes(b"text", value)


@pytest.mark.parametrize("value", ["../outside.txt", "C:/outside.txt", "/etc/passwd", "blobs/../../outside"])
def test_storage_original_path_rejects_escape(tmp_path, value):
    from great_agent.knowledge.storage import KnowledgeStorage

    with pytest.raises(ValueError):
        KnowledgeStorage(tmp_path / "knowledge").original_path(value)


def test_storage_original_path_rejects_symlink_escape(tmp_path):
    from great_agent.knowledge.storage import KnowledgeStorage

    root, outside = tmp_path / "knowledge", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not permit symlink creation")
    with pytest.raises(ValueError, match="outside|escape"):
        KnowledgeStorage(root).original_path("link/secret.txt")


def test_markdown_sections_and_chunks_retain_complete_short_overlap(tmp_path):
    from great_agent.knowledge.parsers import parse_document

    short = "Short explanatory context before the next paragraph."
    first = "alpha " * 400
    second = "beta " * 400
    source = tmp_path / "notes.md"
    source.write_text(f"# First\n\n{first}\n\n{short}\n\n{second}\n\n## Second\n\nDifferent section.", encoding="utf-8")
    parsed = parse_document(source, "text/markdown")
    assert parsed.parser.name == "text"
    assert parsed.pages[0].raw_text.startswith("# First")
    assert parsed.pages[0].width is None
    assert len(parsed.chunks) >= 3
    assert parsed.chunks[0].section == "First"
    assert parsed.chunks[1].text.startswith(short)
    assert parsed.chunks[-1].section == "Second"
    assert "alpha " * 30 not in parsed.chunks[1].text
    assert all(c.locations[0].bbox is None for c in parsed.chunks)


def test_markdown_heading_without_blank_line_does_not_swallow_paragraph(tmp_path):
    from great_agent.knowledge.parsers import parse_document

    source = tmp_path / "notes.md"
    source.write_text("# Results\nThe measured position is precise.\n\n## Limits\nMore observations are needed.", encoding="utf-8")
    parsed = parse_document(source, "text/markdown")
    assert [chunk.section for chunk in parsed.chunks] == ["Results", "Limits"]
    assert parsed.chunks[0].text == "The measured position is precise."


@pytest.mark.parametrize("data", [b"", b"\x00\x01garbled", b"\xff\xfe\x80"])
def test_text_parser_rejects_empty_binary_or_non_utf8(tmp_path, data):
    from great_agent.knowledge.parsers import parse_document

    source = tmp_path / "bad.txt"
    source.write_bytes(data)
    with pytest.raises(ValueError, match="empty|UTF-8|binary"):
        parse_document(source, "text/plain")


def test_pdf_keeps_raw_page_and_top_left_coordinates(tmp_path):
    from great_agent.knowledge.parsers import parse_document

    source = make_pdf(tmp_path / "paper.pdf", [[(40, 750, "1 Introduction"), (40, 720, "Precise positioning uses multiple satellite signals.")]])
    parsed = parse_document(source, "application/pdf")
    assert parsed.pages[0].width == 600
    assert parsed.pages[0].height == 800
    assert "Precise positioning" in parsed.pages[0].raw_text
    paragraph = next(e for e in parsed.elements if "Precise positioning" in e.text)
    assert 65 < paragraph.locations[0].bbox[1] < 90
    assert paragraph.structure["bbox_source"] == "pdfplumber.extract_words"
    assert parsed.chunks[0].locations[0].page_index == 0
    assert parsed.warnings
    assert parsed.model_dump(mode="json")["parser"]["name"] == "pdfplumber"


@pytest.mark.parametrize("lines", [[], [(40, 750, "1")]])
def test_pdf_scan_or_near_empty_is_explicit_failure(tmp_path, lines):
    from great_agent.knowledge.parsers import parse_document

    source = make_pdf(tmp_path / "scan.pdf", [lines])
    with pytest.raises(ValueError, match="OCR|empty|scanned"):
        parse_document(source, "application/pdf")


def test_pdf_page_limit_is_not_silently_truncated(tmp_path):
    from great_agent.knowledge.parsers import parse_document

    source = make_pdf(tmp_path / "long.pdf", [[(40, 750, "A real complete paragraph of useful document text.")]] * 2)
    with pytest.raises(ValueError, match="page.*limit"):
        parse_document(source, "application/pdf", max_pages=1)


def test_pdf_repeated_page_numbers_do_not_make_scanned_document_indexable(tmp_path):
    from great_agent.knowledge.parsers import parse_document

    source = make_pdf(tmp_path / "scan.pdf", [[(40, 750, str(index))] for index in range(30)])
    with pytest.raises(ValueError, match="OCR|scanned"):
        parse_document(source, "application/pdf")


def test_pdf_two_columns_expose_heuristic_quality(tmp_path):
    from great_agent.knowledge.parsers import parse_document

    source = make_pdf(tmp_path / "columns.pdf", [[(40, 750 - 20 * i, f"Left column statement {i}.") for i in range(5)] + [(330, 750 - 20 * i, f"Right column explanation {i}.") for i in range(5)]])
    parsed = parse_document(source, "application/pdf")
    text = "\n".join(e.text for e in parsed.elements)
    assert text.index("Left column statement 4") < text.index("Right column explanation 0")
    assert any("column" in warning.lower() for warning in parsed.warnings)
    assert any("reading_order_heuristic" in chunk.quality_flags for chunk in parsed.chunks)


def test_pdf_last_publisher_terms_page_keeps_source_but_is_not_indexed(tmp_path):
    from great_agent.knowledge.parsers import parse_document

    source = make_pdf(tmp_path / "paper.pdf", [
        [(40, 750, "The research estimates precise satellite positions.")],
        [(40, 750, "Terms and Conditions"),
         (40, 720, "Springer Nature journal content is supplied for personal non-commercial use."),
         (40, 690, "Copyright and terms of use apply to this journal content.")]])
    parsed = parse_document(source, "application/pdf")
    assert parsed.pages[1].role == "publisher_terms"
    assert parsed.pages[1].raw_text.startswith("Terms and Conditions")
    assert (parsed.pages[1].width, parsed.pages[1].height, parsed.pages[1].page_label) == (600, 800, "2")
    assert all(loc.page_index == 0 for e in parsed.elements for loc in e.locations)
    assert all(loc.page_index == 0 for c in parsed.chunks for loc in c.locations)
    assert all("non-commercial" not in c.text for c in parsed.chunks)
    assert any("publisher terms" in warning.lower() for warning in parsed.warnings)


@pytest.mark.parametrize("case", ["non_final", "body_mention", "no_publisher", "no_policy"])
def test_pdf_terms_words_or_research_heading_are_not_excluded(tmp_path, case):
    from great_agent.knowledge.parsers import parse_document

    research = [(40, 750, "The model estimates satellite positions under uncertain conditions.")]
    terms = [(40, 750, "Terms and Conditions"),
             (40, 720, "Springer Nature journal content is subject to copyright and terms of use.")]
    if case == "non_final":
        pages, expected = [terms, research], "journal content"
    elif case == "body_mention":
        pages, expected = [research, [(40, 750, "Research limitations"),
            (40, 720, "Terms and Conditions of Springer Nature journal content are discussed here.")]], "discussed here"
    elif case == "no_publisher":
        pages, expected = [research, [(40, 750, "Terms and Conditions"),
            (40, 720, "The model defines the terms of use of satellite phase data under these conditions.")]], "satellite phase"
    else:
        pages, expected = [research, [(40, 750, "Terms and Conditions"),
            (40, 720, "Springer Nature publishes scientific research concerning satellite phase data.")]], "scientific research"
    parsed = parse_document(make_pdf(tmp_path / "paper.pdf", pages), "application/pdf")
    assert all(page.role == "content" for page in parsed.pages)
    assert any(expected in chunk.text for chunk in parsed.chunks)


def test_pdf_tables_equations_preserve_atomic_blocks_and_warnings(tmp_path):
    from great_agent.knowledge.parsers import parse_document

    source = make_pdf(tmp_path / "table.pdf", [[(40, 750, "Table 1. Satellite signal comparison."), (45, 645, "Signal"), (155, 645, "Value"), (45, 625, "GPS"), (155, 625, "12"), (45, 605, "BDS"), (155, 605, "15"), (40, 570, "x = y + z (1)")]], grid=True)
    parsed = parse_document(source, "application/pdf")
    table = next(e for e in parsed.elements if e.kind == "table")
    assert table.structure["rows"] == [["Signal", "Value"], ["GPS", "12"], ["BDS", "15"]]
    assert any(c.kind == "table" and table.id in c.element_ids for c in parsed.chunks)
    assert any(c.kind == "formula" for c in parsed.chunks)
    assert any("table" in warning.lower() for warning in parsed.warnings)
    assert any("formula" in warning.lower() for warning in parsed.warnings)


def test_docling_normalizer_keeps_native_structure_and_multi_page_provenance():
    from great_agent.knowledge.parsers import normalize_docling_document

    fixture = {"schema_name": "DoclingDocument", "version": "1.8.0", "name": "fixture", "pages": {"1": {"page_no": 1, "size": {"width": 600, "height": 800}}, "2": {"page_no": 2, "size": {"width": 600, "height": 800}}}, "body": {"children": [{"$ref": "#/texts/0"}, {"$ref": "#/tables/0"}]}, "texts": [{"self_ref": "#/texts/0", "label": "section_header", "text": "Results", "prov": [{"page_no": 1, "bbox": {"l": 40, "t": 750, "r": 200, "b": 730, "coord_origin": "BOTTOMLEFT"}}]}], "tables": [{"self_ref": "#/tables/0", "label": "table", "data": {"num_rows": 1, "num_cols": 1, "table_cells": [{"text": "precision", "start_row_offset_idx": 0, "end_row_offset_idx": 1, "start_col_offset_idx": 0, "end_col_offset_idx": 1}]}, "prov": [{"page_no": 1, "bbox": {"l": 40, "t": 700, "r": 400, "b": 600, "coord_origin": "BOTTOMLEFT"}}, {"page_no": 2, "bbox": {"l": 40, "t": 50, "r": 400, "b": 160, "coord_origin": "TOPLEFT"}}]}]}
    parsed = normalize_docling_document(fixture)
    table = next(e for e in parsed.elements if e.kind == "table")
    assert table.locations[0].bbox == (40, 100, 400, 200)
    assert table.locations[1].bbox == (40, 50, 400, 160)
    assert table.structure == fixture["tables"][0]["data"]
    assert parsed.raw_document == fixture
    assert parsed.chunks[0].section == "Results"
    assert len(parsed.pages) == 2


def test_docling_requires_explicit_local_artifacts_without_downloading(tmp_path):
    from great_agent.knowledge.parsers import parse_document

    source = make_pdf(tmp_path / "paper.pdf", [[(40, 750, "A meaningful document which needs optional local models.")]])
    with pytest.raises(ValueError, match="artifacts"):
        parse_document(source, "application/pdf", parser_name="docling")
    artifacts = tmp_path / "models"
    artifacts.mkdir()
    with pytest.raises(ValueError, match="model|dependenc|Docling"):
        parse_document(source, "application/pdf", parser_name="docling", artifacts_path=artifacts)


def test_docling_last_publisher_terms_page_is_preserved_in_raw_json_only():
    from great_agent.knowledge.parsers import normalize_docling_document

    fixture = {"pages": {str(i): {"page_no": i, "size": {"width": 600, "height": 800}} for i in (1, 2)},
        "texts": [{"label": label, "text": text, "prov": [{"page_no": page_no,
            "bbox": {"l": 40, "t": top, "r": 500, "b": top + 20, "coord_origin": "TOPLEFT"}}]}
            for page_no, label, text, top in [(1, "text", "The research estimates precise satellite positions.", 50),
                (2, "section_header", "Terms and Conditions", 50),
                (2, "text", "Springer Nature journal content is supplied for personal non-commercial use; copyright applies.", 80)]]}
    parsed = normalize_docling_document(fixture)
    assert parsed.raw_document == fixture
    assert parsed.pages[1].role == "publisher_terms"
    assert "copyright" in parsed.pages[1].raw_text
    assert all(loc.page_index == 0 for e in parsed.elements for loc in e.locations)
    assert all(loc.page_index == 0 for c in parsed.chunks for loc in c.locations)


def test_docling_rejects_unknown_bbox_coordinate_origin():
    from great_agent.knowledge.parsers import normalize_docling_document

    fixture = {"pages": {"1": {"page_no": 1, "size": {"width": 100, "height": 200}}},
        "texts": [{"text": "A complete source paragraph.", "label": "text", "prov": [{"page_no": 1,
            "bbox": {"l": 1, "t": 2, "r": 90, "b": 20, "coord_origin": "UNKNOWN"}}]}]}
    with pytest.raises(ValueError, match="origin"):
        normalize_docling_document(fixture)


@pytest.mark.parametrize("mutation", ["nan", "outside", "missing_page", "missing_element", "duplicate_page", "missing_bbox"])
def test_parsed_document_rejects_invalid_coordinates_and_references(mutation):
    from great_agent.knowledge.types import ParsedDocument

    payload = {"parser": {"name": "fixture", "version": "1", "config": {}}, "metadata": {}, "pages": [{"page_index": 0, "page_label": "1", "width": 100, "height": 200, "role": "content", "raw_text": "source"}], "elements": [{"id": "e1", "kind": "paragraph", "text": "source", "section": None, "locations": [{"page_index": 0, "page_label": "1", "bbox": [0, 0, 20, 10]}], "quality_flags": []}], "chunks": [{"ordinal": 0, "kind": "text", "section": None, "text": "source", "locations": [{"page_index": 0, "page_label": "1", "bbox": [0, 0, 20, 10]}], "element_ids": ["e1"], "quality_flags": []}], "warnings": []}
    if mutation == "nan":
        payload["elements"][0]["locations"][0]["bbox"][1] = float("nan")
    elif mutation == "outside":
        payload["chunks"][0]["locations"][0]["bbox"][2] = 101
    elif mutation == "missing_page":
        payload["elements"][0]["locations"][0]["page_index"] = 1
    elif mutation == "missing_element":
        payload["chunks"][0]["element_ids"] = ["missing"]
    elif mutation == "missing_bbox":
        payload["chunks"][0]["locations"][0]["bbox"] = None
    else:
        payload["pages"].append(payload["pages"][0])
    with pytest.raises(ValidationError):
        ParsedDocument.model_validate(payload)


def test_parse_worker_writes_validated_json_and_small_error(tmp_path):
    source = tmp_path / "paper.txt"
    source.write_text("The complete local document is parsed without an API call.", encoding="utf-8")
    output = tmp_path / "parsed.json"
    command = [sys.executable, "-m", "great_agent.knowledge.parse_worker", "--source", str(source), "--media-type", "text/plain", "--parser", "pdfplumber", "--output", str(output), "--max-pages", "300"]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["chunks"][0]["text"].startswith("The complete")
    source.write_bytes(b"bad\x00binary")
    completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert completed.returncode != 0
    assert len(completed.stderr) < 4096
    assert "error" in json.loads(output.read_text(encoding="utf-8"))

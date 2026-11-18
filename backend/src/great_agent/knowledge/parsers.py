"""Generic local extraction and paragraph chunking with explicit quality limits."""
from __future__ import annotations

import importlib.metadata
from itertools import islice
import math
import os
import re
from pathlib import Path
from statistics import median

from great_agent.knowledge.types import (
    Chunk, Element, Location, MAX_DOCUMENT_PAGES, MAX_SOURCE_BYTES,
    Page, ParsedDocument, ParserInfo,
)


CHUNKER_VERSION = "paragraph-1"
PUBLISHER_TERMS_FILTER_VERSION = "last-page-title-publisher-1"
TARGET_TOKENS = 700
OVERLAP_TOKENS = 100
BASELINE_FLAGS = ["reading_order_heuristic", "section_detection_heuristic"]


def estimate_tokens(text: str) -> int:
    """A conservative estimate, not a model-specific tokenizer."""
    cjk = sum("\u2e80" <= char <= "\u9fff" for char in text)
    return max(1, math.ceil((len(text) - cjk) / 4 + cjk))


def _unique_locations(elements: list[Element]) -> list[Location]:
    seen, result = set(), []
    for element in elements:
        for loc in element.locations:
            key = (loc.page_index, loc.page_label, loc.bbox)
            if key not in seen:
                seen.add(key)
                result.append(loc)
    return result


def _split_long_paragraph(text: str) -> list[str]:
    if estimate_tokens(text) <= TARGET_TOKENS:
        return [text]
    # Prefer sentence boundaries; a pathological long sentence falls back to words.
    units = re.split(r"(?<=[.!?。！？])\s+", text)
    if len(units) == 1:
        units = re.findall(r"\S+\s*", text)
    pieces, current = [], ""
    for unit in units:
        if estimate_tokens(unit) > TARGET_TOKENS:
            if current:
                pieces.append(current.strip())
                current = ""
            # CJK/no-whitespace text must still respect the estimated token budget.
            segment = ""
            for char in unit:
                if estimate_tokens(segment + char) > TARGET_TOKENS:
                    pieces.append(segment)
                    segment = ""
                segment += char
            if segment:
                current = segment
        elif current and estimate_tokens(current + " " + unit) > TARGET_TOKENS:
            pieces.append(current.strip())
            current = unit
        else:
            current += (" " if current else "") + unit
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _chunks(elements: list[Element]) -> list[Chunk]:
    chunks: list[Chunk] = []
    pending: list[tuple[Element, str, bool]] = []
    section = None

    def emit(parts: list[tuple[Element, str, bool]], kind="text"):
        owners = list({element.id: element for element, _, _ in parts}.values())
        flags = sorted({flag for owner in owners for flag in owner.quality_flags})
        value = "\n\n".join(text for _, text, _ in parts).strip()
        if not value:
            return
        if kind != "text" and estimate_tokens(value) > TARGET_TOKENS:
            flags.append("oversized_atomic_block")
        chunks.append(Chunk(ordinal=len(chunks), kind=kind, section=owners[0].section,
                            text=value, locations=_unique_locations(owners),
                            element_ids=[owner.id for owner in owners], quality_flags=flags))

    def flush():
        nonlocal pending
        if pending:
            emit(pending)
            pending = []

    for index, element in enumerate(elements):
        if element.kind == "heading":
            flush()
            section = element.section
            continue
        if element.kind == "picture" or not element.text.strip():
            continue
        if element.section != section:
            flush()
            section = element.section
        if element.kind in {"table", "formula", "code"}:
            flush()
            parts = [(element, element.text, True)]
            # Keep a short neighboring caption/explanation and its provenance.
            for neighbor_index in (index - 1, index + 1):
                if not 0 <= neighbor_index < len(elements):
                    continue
                neighbor = elements[neighbor_index]
                if (neighbor.kind in {"paragraph", "caption"} and neighbor.section == element.section
                        and estimate_tokens(neighbor.text) <= OVERLAP_TOKENS):
                    if neighbor_index < index:
                        parts.insert(0, (neighbor, neighbor.text, True))
                    else:
                        parts.append((neighbor, neighbor.text, True))
            emit(parts, element.kind)
            continue
        pieces = _split_long_paragraph(element.text)
        for piece in pieces:
            complete = len(pieces) == 1
            pending_text = "\n\n".join(text for _, text, _ in pending)
            if pending and estimate_tokens(pending_text + "\n\n" + piece) > TARGET_TOKENS:
                emit(pending)
                overlap, overlap_size = [], 0
                for prior in reversed(pending):
                    cost = estimate_tokens(prior[1])
                    if not prior[2] or overlap_size + cost > OVERLAP_TOKENS:
                        break
                    overlap.insert(0, prior)
                    overlap_size += cost
                if overlap and estimate_tokens("\n\n".join(p[1] for p in overlap) + "\n\n" + piece) > TARGET_TOKENS:
                    overlap = []
                pending = overlap
            pending.append((element, piece, complete))
    flush()
    return chunks


def _parser_info(name: str, version: str, **config) -> ParserInfo:
    return ParserInfo(name=name, version=version, config={"chunker_version": CHUNKER_VERSION,
        "target_estimated_tokens": TARGET_TOKENS, "overlap_estimated_tokens": OVERLAP_TOKENS,
        "bbox_coordinates": "pdf_points_top_left", **config})


def _formula(text: str) -> bool:
    return len(text) < 500 and bool(re.search(r"\S\s*(?:=|≈|≠|≤|≥)\s*\S", text))


def _exclude_publisher_terms(pages: list[Page], elements: list[Element], warnings: list[str]) -> list[Element]:
    """Conservatively identify a publisher's appended legal sheet, never body mentions."""
    if not pages:
        return elements
    page = pages[-1]
    lines = [line.strip() for line in page.raw_text.splitlines() if line.strip()]
    if not lines or not re.fullmatch(r"Terms\s+and\s+Conditions[.:]?", lines[0], re.I):
        return elements
    body = " ".join(lines[1:])
    publisher = re.search(r"\b(?:Springer\s+Nature|Elsevier|(?:John\s+)?Wiley|Taylor\s*(?:&|and)\s*Francis|"
        r"SAGE\s+Publications|Oxford\s+University\s+Press|Cambridge\s+University\s+Press)\b", body, re.I)
    legal_markers = re.findall(r"journal\s+content|terms\s+of\s+use|non[ -]commercial|copyright|"
        r"licen[cs]e|privacy\s+policy|personal\s+data|all\s+rights\s+reserved", body, re.I)
    if not publisher or len({marker.lower() for marker in legal_markers}) < 2:
        return elements
    page.role = "publisher_terms"
    warnings.append(f"Page {page.page_label} contains publisher terms and was excluded from scientific elements/chunks; raw page text and page dimensions are retained.")
    # Do not retain a mixed-page block containing legal prose in a scientific chunk.
    return [element for element in elements if all(loc.page_index != page.page_index for loc in element.locations)]


def _parse_text(source: Path, media_type: str) -> ParsedDocument:
    try:
        raw = source.read_text(encoding="utf-8-sig")
    except UnicodeError as exc:
        raise ValueError("text documents must be valid UTF-8") from exc
    if not raw.strip():
        raise ValueError("empty text document")
    if any(ord(char) < 32 and char not in "\t\n\r\f" for char in raw):
        raise ValueError("binary content is not a text document")
    page = Page(page_index=0, page_label="1", raw_text=raw)
    elements, section, warnings = [], None, []
    blocks = re.findall(r"(?m)^#{1,6}[ \t]+[^\n]*|```[^\n]*\n[\s\S]*?```|\$\$[\s\S]*?\$\$|[^\n]+(?:\n(?!\s*\n|#{1,6}\s)[^\n]+)*", raw)
    for block in blocks:
        text = block.strip()
        if not text:
            continue
        kind, flags, structure = "paragraph", [], None
        if media_type == "text/markdown" and re.match(r"^#{1,6}\s+", text):
            kind, section = "heading", re.sub(r"^#{1,6}\s+", "", text).strip()
        elif text.startswith("```"):
            kind = "code"
        elif text.startswith("$$") or _formula(text):
            kind, flags = "formula", ["formula_unverified"]
            warnings.append("Formula transcription and mathematical semantics are unverified.")
        elif re.search(r"(?m)^\s*\|?\s*:?-{3,}.*\|", text):
            kind, flags = "table", ["table_structure_unverified"]
            structure = {"rows": [[cell.strip() for cell in line.strip().strip("|").split("|")]
                                  for line in text.splitlines() if not re.match(r"^[\s|:\-]+$", line)]}
            warnings.append("Markdown table structure is heuristic and unverified.")
        elements.append(Element(id=f"e{len(elements)}", kind=kind, text=text, section=section,
            locations=[Location(page_index=0, page_label="1")], quality_flags=flags, structure=structure))
    chunks = _chunks(elements)
    if not chunks:
        raise ValueError("empty text document has no indexable paragraphs")
    return ParsedDocument(parser=_parser_info("text", "1", media_type=media_type),
        metadata={"media_type": media_type}, pages=[page], elements=elements, chunks=chunks,
        warnings=list(dict.fromkeys(warnings)))


def _box(box, page) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = [float(value) for value in box]
    left, top = page.bbox[:2]
    return (max(0, x0 - left), max(0, y0 - top), min(page.width, x1 - left), min(page.height, y1 - top))


def _page_geometry(page) -> dict:
    """Map pdfplumber's rotated MediaBox frame back to native PDF points.

    Store this inside the parser process. API/source preview must never reopen
    arbitrary PDFs in the API process merely to recover their coordinate frame.
    PDF.js subsequently handles CropBox, rotation, zoom and UserUnit itself.
    """
    mx0,my0,mx1,my1=(float(v) for v in page.page_obj.mediabox)
    rotation=page.rotation
    transforms={0:[1,0,0,-1,mx0,my1],90:[0,1,1,0,mx0,my0],
                180:[-1,0,0,1,mx1,my0],270:[0,-1,-1,0,mx1,my1]}
    if (rotation not in transforms or not all(math.isfinite(v) for v in (mx0,my0,mx1,my1))
            or mx0>=mx1 or my0>=my1):
        return {'coordinate_space':'unverified_top_left'}
    return {'coordinate_space':'rotated_media_top_left','rotation':rotation,
            'to_pdf_transform':transforms[rotation]}


def _line_groups(words: list[dict]) -> list[list[dict]]:
    lines: list[list[dict]] = []
    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if not lines or abs(lines[-1][0]["top"] - word["top"]) > 3:
            lines.append([word])
        else:
            lines[-1].append(word)
    for line in lines:
        line.sort(key=lambda w: w["x0"])
    return lines


def _column_groups(words: list[dict], width: float) -> tuple[list[list[dict]], bool]:
    lines = _line_groups(words)
    candidates = []
    for line in lines:
        for left, right in zip(line, line[1:]):
            gap = right["x0"] - left["x1"]
            middle = (right["x0"] + left["x1"]) / 2
            if gap > width * .08 and width * .3 < middle < width * .7:
                candidates.append(middle)
    if len(candidates) < 3:
        return [words], False
    split = median(candidates)
    left = [word for word in words if (word["x0"] + word["x1"]) / 2 < split]
    right = [word for word in words if (word["x0"] + word["x1"]) / 2 >= split]
    if min(len(left), len(right)) < 5:
        return [words], False
    return [left, right], True


def _parse_pdf(source: Path, max_pages: int) -> ParsedDocument:
    try:
        import pdfplumber
        from pdfminer.pdfdocument import PDFNoPageLabels
    except ImportError as exc:
        raise ValueError("pdfplumber dependency is unavailable") from exc
    from great_agent.knowledge.formula_recognition import numbered_formula_regions
    page_geometry={}
    equation_regions=[]
    pages, elements, warnings, section = [], [], [
        "PDF reading order and section detection use generic heuristics; verify source pages.",
        "Formulas, table values and image content have not been semantically verified."], None
    try:
        with pdfplumber.open(source) as pdf:
            if len(pdf.pages) > max_pages:
                raise ValueError(f"PDF page count exceeds page limit ({max_pages})")
            labels = []
            try:
                # pdfminer deliberately returns an unbounded page-label iterator.
                labels = list(islice(pdf.doc.get_page_labels(), len(pdf.pages)))
            except (AttributeError, KeyError, PDFNoPageLabels):
                pass
            for index, page in enumerate(pdf.pages):
                label = str(labels[index]) if index < len(labels) else str(index + 1)
                page_geometry[str(index)]=_page_geometry(page)
                equation_regions.extend({**region, 'page_index':index}
                    for region in numbered_formula_regions(page))
                raw = page.extract_text() or ""
                if len(raw) > 2_000_000:
                    raise ValueError("page text exceeds extraction size limit")
                role = "content" if raw.strip() else ("image_only" if page.images else "empty")
                pages.append(Page(page_index=index, page_label=label, width=page.width, height=page.height,
                                  role=role, raw_text=raw))
                if len(re.sub(r"\W", "", raw)) < 20:
                    warnings.append(f"Page {label} is empty or nearly empty; OCR may be required.")
                def location(bbox):
                    return [Location(page_index=index, page_label=label, bbox=_box(bbox, page))]

                def add(kind, text, bbox, flags=None, structure=None):
                    elements.append(Element(id=f"e{len(elements)}", kind=kind, text=text, section=section,
                        locations=location(bbox), quality_flags=BASELINE_FLAGS + (flags or []), structure=structure))

                tables = page.find_tables()
                table_boxes = [table.bbox for table in tables]
                words = page.extract_words(extra_attrs=["size", "fontname"])
                if len(words) > 100000:
                    raise ValueError("page word count exceeds extraction size limit")
                words = [word for word in words if not any(box[0] <= (word["x0"] + word["x1"]) / 2 <= box[2]
                    and box[1] <= (word["top"] + word["bottom"]) / 2 <= box[3] for box in table_boxes)]
                groups, columns = _column_groups(words, page.width)
                if columns:
                    warnings.append(f"Page {label} has suspected multiple columns; reading order is heuristic.")
                blocks = []
                for group_index, group in enumerate(groups):
                    lines = _line_groups(group)
                    current, previous_bottom = [], None
                    for line in lines:
                        text = " ".join(word["text"] for word in line)
                        top, bottom = min(w["top"] for w in line), max(w["bottom"] for w in line)
                        heading = bool(re.match(r"^\d+(?:\.\d+)*[.)]?\s+[A-Z]", text)) and len(text) < 140
                        heading = heading or (len(text) < 100 and all("Bold" in w.get("fontname", "") for w in line))
                        kind = "heading" if heading else ("formula" if _formula(text) else ("caption" if re.match(r"^(?:Table|Fig(?:ure)?\.?)[ \d]", text, re.I) else "paragraph"))
                        gap = top - previous_bottom if previous_bottom is not None else 0
                        if current and (gap > 8 or kind != "paragraph" or current[0][1] != "paragraph"):
                            blocks.append((group_index, current))
                            current = []
                        current.append((line, kind, text))
                        previous_bottom = bottom
                    if current:
                        blocks.append((group_index, current))
                # Insert table blocks geometrically within the detected reading column.
                ordered = []
                for group_index, block in blocks:
                    flattened = [word for line, _, _ in block for word in line]
                    bbox = (min(w["x0"] for w in flattened), min(w["top"] for w in flattened), max(w["x1"] for w in flattened), max(w["bottom"] for w in flattened))
                    ordered.append((group_index, bbox[1], block[0][1], "\n".join(text for _, _, text in block), bbox,
                        {"bbox_source": "pdfplumber.extract_words", "bbox_kind": "word_union"}))
                for table in tables:
                    rows = [[cell or "" for cell in row] for row in table.extract()]
                    group_index = 1 if columns and table.bbox[0] > page.width / 2 else 0
                    ordered.append((group_index, table.bbox[1], "table", "\n".join(" | ".join(row) for row in rows), table.bbox, {"rows": rows, "source": "pdfplumber.find_tables"}))
                    warnings.append(f"Page {label} table extraction is heuristic; table structure and numbers require verification.")
                for _, _, kind, text, bbox, structure in sorted(ordered, key=lambda b: (b[0], b[1])):
                    flags = []
                    if kind == "heading":
                        section = text[:2048]
                    elif kind == "formula":
                        flags.append("formula_unverified")
                        warnings.append(f"Page {label} formula transcription is unverified.")
                    elif kind == "table":
                        flags.append("table_structure_unverified")
                    add(kind, text, bbox, flags, structure)
                for image in page.images:
                    add("picture", "", (image["x0"], image["top"], image["x1"], image["bottom"]),
                        ["image_content_not_extracted"], {"source": "pdfplumber.images"})
                page.close()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"PDF extraction failed ({type(exc).__name__}); document may be malformed") from exc
    if not any(len(re.sub(r"\W", "", page.raw_text)) >= 20 for page in pages):
        raise ValueError("PDF is scanned or nearly empty; OCR is required before indexing")
    elements = _exclude_publisher_terms(pages, elements, warnings)
    chunks = _chunks(elements)
    if not chunks:
        raise ValueError("PDF has no indexable text; OCR may be required")
    return ParsedDocument(parser=_parser_info("pdfplumber", pdfplumber.__version__, max_pages=max_pages,
        page_coordinate_version='pdfplumber-media-1',
        equation_region_version='numbered-formula-region-1',
        publisher_terms_filter_version=PUBLISHER_TERMS_FILTER_VERSION),
        metadata={"media_type": "application/pdf",'page_geometry':page_geometry,
                  'equation_regions':equation_regions}, pages=pages, elements=elements, chunks=chunks,
        warnings=list(dict.fromkeys(warnings)))


def normalize_docling_document(raw: dict, *, parser_version: str = "fixture", max_pages: int = MAX_DOCUMENT_PAGES) -> ParsedDocument:
    """Normalize native JSON; fixture verification does not certify a model run."""
    native_pages = sorted(raw.get("pages", {}).values(), key=lambda p: p["page_no"])
    if not native_pages or len(native_pages) > max_pages:
        raise ValueError("Docling page count is empty or exceeds page limit")
    page_indices = {page["page_no"]: index for index, page in enumerate(native_pages)}
    pages = [Page(page_index=index, page_label=str(page.get("page_label", page["page_no"])),
        width=page["size"]["width"], height=page["size"]["height"], raw_text="")
        for index, page in enumerate(native_pages)]
    lookup = {}
    for collection in ("texts", "tables", "pictures", "groups"):
        for index, item in enumerate(raw.get(collection, [])):
            lookup[f"#/{collection}/{index}"] = item
    ordered, seen = [], set()
    def walk(ref, depth=0):
        if depth > 100:
            raise ValueError("Docling reference depth exceeds limit")
        if ref in seen:
            return
        seen.add(ref)
        item = lookup.get(ref)
        if item is None:
            raise ValueError("Docling body references an unknown element")
        if "label" in item and item.get("prov"):
            ordered.append(item)
        for child in item.get("children", []):
            walk(child["$ref"], depth + 1)
    for child in raw.get("body", {}).get("children", []):
        walk(child["$ref"])
    for ref, item in lookup.items():
        if ref not in seen and item.get("prov"):
            ordered.append(item)
    elements, section = [], None
    for item in ordered:
        label = item.get("label", "text")
        kind = {"section_header": "heading", "title": "heading", "table": "table", "formula": "formula",
            "picture": "picture", "caption": "caption", "list_item": "list", "code": "code", "footnote": "footnote"}.get(label, "paragraph")
        structure = item.get("data") if kind == "table" else None
        text = item.get("text", "")
        if kind == "table":
            text = "\n".join(cell.get("text", "") for cell in (structure or {}).get("table_cells", []))
        if kind == "heading":
            section = text[:2048]
        locations = []
        for prov in item.get("prov", []):
            index = page_indices.get(prov["page_no"])
            if index is None:
                raise ValueError("Docling provenance references an unknown page")
            bbox, page = prov["bbox"], pages[index]
            origin = bbox.get("coord_origin", "TOPLEFT").upper()
            if origin not in {"TOPLEFT", "BOTTOMLEFT"}:
                raise ValueError("Docling bbox coordinate origin is unknown")
            if origin == "BOTTOMLEFT":
                box = (bbox["l"], page.height - bbox["t"], bbox["r"], page.height - bbox["b"])
            else:
                box = (bbox["l"], bbox["t"], bbox["r"], bbox["b"])
            locations.append(Location(page_index=index, page_label=page.page_label, bbox=box))
            pages[index].raw_text += ("\n" if pages[index].raw_text else "") + text
        flags = ["table_structure_unverified"] if kind == "table" else (["formula_unverified"] if kind == "formula" else [])
        elements.append(Element(id=str(item.get("self_ref", f"e{len(elements)}")), kind=kind, text=text,
            section=section, locations=locations, quality_flags=flags, structure=structure))
    warnings = ["Docling formula, table values and picture semantics require source verification."]
    elements = _exclude_publisher_terms(pages, elements, warnings)
    chunks = _chunks(elements)
    if not chunks:
        raise ValueError("Docling produced no indexable text; OCR may be required")
    return ParsedDocument(parser=_parser_info("docling", parser_version,
        publisher_terms_filter_version=PUBLISHER_TERMS_FILTER_VERSION), metadata={"media_type": "application/pdf"},
        pages=pages, elements=elements, chunks=chunks, raw_document=raw,
        warnings=warnings)


def _parse_docling(source: Path, max_pages: int, artifacts_path: Path | None) -> ParsedDocument:
    if artifacts_path is None or not Path(artifacts_path).is_dir():
        raise ValueError("Docling requires an explicit local artifacts directory; model downloads are disabled")
    artifacts = Path(artifacts_path).resolve()
    if not any(path.suffix.lower() in {".safetensors", ".pt", ".pth", ".onnx"} for path in artifacts.rglob("*")):
        raise ValueError("Docling local model artifacts are missing; model downloads are disabled")
    # Hugging Face libraries must fail on incomplete local assets instead of fetching.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as exc:
        raise ValueError("Docling optional dependencies are unavailable") from exc
    try:
        options = PdfPipelineOptions(artifacts_path=artifacts, enable_remote_services=False,
            allow_external_plugins=False, do_ocr=False, do_table_structure=True)
        converter = DocumentConverter(allowed_formats=[InputFormat.PDF],
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        result = converter.convert(source, max_num_pages=max_pages, max_file_size=MAX_SOURCE_BYTES)
        parsed = normalize_docling_document(result.document.export_to_dict(),
            parser_version=importlib.metadata.version("docling"), max_pages=max_pages)
        parsed.parser.config.update({"artifacts_path": str(artifacts), "do_ocr": False,
            "enable_remote_services": False, "offline": True})
        return parsed
    except Exception as exc:
        raise ValueError(f"Docling local conversion failed ({type(exc).__name__}); check installed dependencies and complete local model artifacts; downloads are disabled") from exc


def parse_document(source: Path, media_type: str, parser_name: str = "pdfplumber", max_pages: int = MAX_DOCUMENT_PAGES,
                   artifacts_path: Path | None = None) -> ParsedDocument:
    source = Path(source)
    if parser_name not in {"pdfplumber", "docling"}:
        raise ValueError("unsupported parser")
    if not 1 <= max_pages <= MAX_DOCUMENT_PAGES:
        raise ValueError("page limit must be between 1 and 300")
    if source.stat().st_size > MAX_SOURCE_BYTES:
        raise ValueError("source exceeds document size limit")
    if media_type in {"text/plain", "text/markdown"}:
        return _parse_text(source, media_type)
    if media_type != "application/pdf":
        raise ValueError("unsupported document media type")
    return _parse_docling(source, max_pages, artifacts_path) if parser_name == "docling" else _parse_pdf(source, max_pages)

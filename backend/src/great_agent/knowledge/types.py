"""Validated boundary between an untrusted parser process and the publisher."""
from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


PARSER_OUTPUT_MAX_BYTES = 32 * 1024 * 1024
MAX_DOCUMENT_PAGES = 300
MAX_SOURCE_BYTES = 50 * 1024 * 1024
Text = Annotated[str, Field(max_length=2_000_000)]
Label = Annotated[str, Field(max_length=2048)]
FinitePositive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Coordinate = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ParserInfo(BoundaryModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    version: Annotated[str, Field(min_length=1, max_length=100)]
    config: dict[str, JsonValue] = Field(default_factory=dict)


class Page(BoundaryModel):
    page_index: Annotated[int, Field(ge=0, lt=MAX_DOCUMENT_PAGES)]
    page_label: Label
    width: FinitePositive | None = None
    height: FinitePositive | None = None
    role: Literal["content", "empty", "image_only", "publisher_terms"] = "content"
    raw_text: Text

    @model_validator(mode="after")
    def paired_dimensions(self):
        if (self.width is None) != (self.height is None):
            raise ValueError("page width and height must both be present or absent")
        return self


class Location(BoundaryModel):
    page_index: Annotated[int, Field(ge=0, lt=MAX_DOCUMENT_PAGES)]
    page_label: Label
    # PDF points, top-left origin; text sources deliberately have no invented box.
    bbox: tuple[Coordinate, Coordinate, Coordinate, Coordinate] | None = None


class Element(BoundaryModel):
    id: Annotated[str, Field(min_length=1, max_length=512)]
    kind: Literal["heading", "paragraph", "table", "formula", "picture", "caption", "list", "code", "footnote"]
    text: Text
    section: Label | None = None
    locations: list[Location] = Field(min_length=1, max_length=MAX_DOCUMENT_PAGES)
    quality_flags: list[Label] = Field(default_factory=list, max_length=100)
    structure: dict[str, JsonValue] | None = None


class Chunk(BoundaryModel):
    ordinal: Annotated[int, Field(ge=0, lt=50000)]
    kind: Literal["text", "table", "formula", "code"]
    section: Label | None = None
    text: Annotated[str, Field(min_length=1, max_length=2_000_000)]
    locations: list[Location] = Field(min_length=1, max_length=MAX_DOCUMENT_PAGES * 100)
    element_ids: list[Annotated[str, Field(min_length=1, max_length=512)]] = Field(min_length=1, max_length=100000)
    quality_flags: list[Label] = Field(default_factory=list, max_length=100)


class ParsedDocument(BoundaryModel):
    parser: ParserInfo
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    pages: list[Page] = Field(min_length=1, max_length=MAX_DOCUMENT_PAGES)
    elements: list[Element] = Field(min_length=1, max_length=100000)
    chunks: list[Chunk] = Field(min_length=1, max_length=50000)
    warnings: list[Label] = Field(default_factory=list, max_length=1000)
    raw_document: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_provenance_and_budget(self):
        pages = {page.page_index: page for page in self.pages}
        if sorted(pages) != list(range(len(self.pages))):
            raise ValueError("page indices must be unique and contiguous from zero")
        elements = {element.id: element for element in self.elements}
        if len(elements) != len(self.elements):
            raise ValueError("element ids must be unique")
        if [c.ordinal for c in self.chunks] != list(range(len(self.chunks))):
            raise ValueError("chunk ordinals must be contiguous from zero")
        for item in [*self.elements, *self.chunks]:
            for loc in item.locations:
                page = pages.get(loc.page_index)
                if page is None or page.page_label != loc.page_label:
                    raise ValueError("location refers to an unknown page or mismatched page label")
                if loc.bbox is None and page.width is not None:
                    raise ValueError("physical page locations require a bbox")
                if loc.bbox is not None:
                    if page.width is None or page.height is None:
                        raise ValueError("bbox requires physical page dimensions")
                    x0, y0, x1, y1 = loc.bbox
                    if x1 < x0 or y1 < y0 or x1 > page.width or y1 > page.height:
                        raise ValueError("bbox lies outside page boundaries or is inverted")
        for chunk in self.chunks:
            if any(element_id not in elements for element_id in chunk.element_ids):
                raise ValueError("chunk refers to an unknown element")
        # This also rejects NaN hidden in arbitrary metadata/structure/raw JSON.
        try:
            encoded = json.dumps(self.model_dump(mode="python"), ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("document contains invalid JSON metadata") from exc
        if len(encoded.encode("utf-8")) > PARSER_OUTPUT_MAX_BYTES:
            raise ValueError("parsed document exceeds output size limit")
        return self

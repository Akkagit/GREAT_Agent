"""Isolated parser CLI: writes staging JSON and never opens the application DB."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from great_agent.knowledge.parsers import parse_document
from great_agent.knowledge.types import PARSER_OUTPUT_MAX_BYTES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--media-type", required=True)
    parser.add_argument("--parser", default="pdfplumber", choices=["pdfplumber", "docling"])
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-pages", type=int, default=300)
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args(argv)
    temporary = args.output.with_suffix(args.output.suffix + ".part")
    try:
        parsed = parse_document(args.source, args.media_type, args.parser, args.max_pages, args.artifacts)
        serialized = parsed.model_dump_json().encode("utf-8")
        if len(serialized) > PARSER_OUTPUT_MAX_BYTES:
            raise ValueError("parsed document exceeds output size limit")
        temporary.write_bytes(serialized)
        os.replace(temporary, args.output)
        return 0
    except Exception as exc:
        # Keep operational failures actionable and bounded; no traceback or document dump.
        message = f"{type(exc).__name__}: {exc}"[:1500]
        error = json.dumps({"error": message}, ensure_ascii=False)
        print(error, file=sys.stderr)
        try:
            temporary.write_text(error, encoding="utf-8")
            os.replace(temporary, args.output)
        except OSError:
            pass
        return 1
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())

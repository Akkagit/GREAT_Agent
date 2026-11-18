"""Content-addressed originals with streaming limits and exclusive publication."""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from great_agent.knowledge.types import MAX_SOURCE_BYTES


class KnowledgeStorage:
    def __init__(self, root: Path, max_upload_bytes: int = MAX_SOURCE_BYTES):
        if max_upload_bytes <= 0:
            raise ValueError("upload size limit must be positive")
        self.root = Path(root).resolve()
        self.max_upload_bytes = max_upload_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def original_path(self, relative_path: str) -> Path:
        if not relative_path or any(char in relative_path for char in ("\\", ":", "\x00")):
            raise ValueError("invalid storage relative path")
        relative = PurePosixPath(relative_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("storage path escape")
        resolved = (self.root / relative).resolve()
        if not resolved.is_relative_to(self.root) or resolved == self.root:
            raise ValueError("storage path lies outside root")
        return resolved

    @staticmethod
    def _filename_type(filename: str) -> tuple[str, str]:
        if not filename or len(filename) > 255 or any(ord(c) < 32 or c in '/\\:' for c in filename):
            raise ValueError("invalid upload filename")
        extension = Path(filename).suffix.lower()
        media_type = {".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain"}.get(extension)
        if media_type is None:
            raise ValueError("unsupported upload type; expected PDF, MD or TXT")
        return extension, media_type

    def store_bytes(self, data: bytes, filename: str) -> dict:
        return self.store_stream(io.BytesIO(data), filename)

    def store_file(self, path: Path, filename: str) -> dict:
        """Copy an API-owned staging file without reading the upload into memory."""
        with Path(path).open("rb") as stream:
            return self.store_stream(stream, filename)

    def store_stream(self, stream: BinaryIO, filename: str) -> dict:
        extension, media_type = self._filename_type(filename)
        staging = self.original_path("staging")
        staging.mkdir(parents=True, exist_ok=True)
        staging = self.original_path("staging")
        digest = hashlib.sha256()
        size, prefix = 0, b""
        handle, temporary_name = tempfile.mkstemp(prefix="blob-", suffix=".part", dir=staging)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as destination:
                while block := stream.read(1024 * 1024):
                    size += len(block)
                    if size > self.max_upload_bytes:
                        raise ValueError("upload exceeds size limit")
                    if not prefix:
                        prefix = block[:1024]
                    digest.update(block)
                    destination.write(block)
            if not size:
                raise ValueError("empty upload")
            if media_type == "application/pdf" and not prefix.lstrip().startswith(b"%PDF-"):
                raise ValueError("invalid PDF signature")
            if media_type.startswith("text/") and b"\x00" in prefix:
                raise ValueError("binary content is not a text document")
            sha256 = digest.hexdigest()
            relative = f"blobs/{sha256[:2]}/{sha256}{extension}"
            target = self.original_path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target = self.original_path(relative)
            try:
                # A hard link publishes the complete file without replacing an original.
                os.link(temporary, target)
            except FileExistsError:
                existing = hashlib.sha256()
                with target.open("rb") as prior:
                    while block := prior.read(1024 * 1024):
                        existing.update(block)
                if existing.hexdigest() != sha256:
                    raise ValueError("stored original does not match its content address")
            return {"sha256": sha256, "size_bytes": size, "media_type": media_type, "original_relpath": relative}
        finally:
            temporary.unlink(missing_ok=True)

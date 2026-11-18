from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url: AnyHttpUrl
    name: str = Field(min_length=1)
    response_format: Literal["json_schema", "json_object"] = "json_schema"
    thinking: Literal["enabled", "disabled"] | None = None
    max_tokens: int = Field(default=2048, ge=128, le=32768)
    connect_timeout_seconds: float = Field(default=10, gt=0)
    read_timeout_seconds: float = Field(default=120, gt=0)
    request_timeout_seconds: float = Field(default=180, gt=0)
    api_key: SecretStr = SecretStr("")


class PppfltSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable: Path
    template_path: Path
    observation_root: Path
    product_root: Path
    default_concurrency: int = Field(default=1, ge=1, le=8)
    observation_template: str = (
        r"{year}\{doy:03d}\{station_lower}{doy:03d}0.{yy:02d}o"
    )
    navigation_template: str = r"{year}\{doy:03d}\brdm{year}{doy:03d}.rnx"
    orbit_template: str = r"{year}\{doy:03d}\igs{year}{doy:03d}.sp3"
    clock_template: str = r"{year}\{doy:03d}\igs{year}{doy:03d}.clk"
    bias_template: str = r"{year}\{doy:03d}\igs{year}{doy:03d}.bia"
    ifcb_template: str = r"{year}\{doy:03d}\igs{year}{doy:03d}.ifcb"
    upd_template: str = r"{year}\{doy:03d}\igs{year}{doy:03d}.upd"
    antenna_template: str = r"models\igs20.atx"
    tide_template: str = r"models\oceanload.blq"
    erp_template: str = r"models\finals.erp"
    planetary_template: str = r"models\de430.bsp"


class ConversationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_model_requests: int = Field(default=4, ge=1, le=4)
    max_tool_calls: int = Field(default=6, ge=1, le=6)
    timeout_seconds: float = Field(default=90, gt=0, le=90)


class KnowledgeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    chat_enabled: bool = True
    formula_model_name: str | None = Field(default=None, min_length=1)
    embedding_enabled: bool = False
    embedding_model_path: Path | None = None
    embedding_tokenizer_path: Path | None = None
    embedding_min_available_mb: int = Field(default=384, ge=1)
    root: Path | None = None
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)
    max_pages: int = Field(default=300, ge=1, le=300)
    parser_timeout_seconds: float = Field(default=300, gt=0, le=1800, allow_inf_nan=False)
    poll_interval_seconds: float = Field(default=0.25, gt=0, le=10, allow_inf_nan=False)
    docling_artifacts: Path | None = None


class ResearchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    code_roots: dict[str, Path] = Field(default_factory=dict)
    model: ModelSettings | None = None
    poll_interval_seconds: float = Field(default=0.5, gt=0, le=10)


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["local"] = "local"
    project_root: Path
    run_root: Path
    database_path: Path
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    max_concurrency: int = Field(default=2, ge=1, le=8)
    convergence_hold_seconds: float = Field(default=600, ge=0, allow_inf_nan=False)
    model: ModelSettings
    conversation: ConversationSettings = Field(default_factory=ConversationSettings)
    knowledge: KnowledgeSettings = Field(default_factory=KnowledgeSettings)
    research: ResearchSettings = Field(default_factory=ResearchSettings)
    pppflt: PppfltSettings

    @classmethod
    def load(cls, path: Path, environ: Mapping[str, str]) -> AppSettings:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        raw.setdefault("model", {})["api_key"] = environ.get(
            "GREAT_AGENT_MODEL_API_KEY", ""
        )
        research_model = raw.get('research', {}).get('model')
        if research_model is not None:
            research_model['api_key'] = environ.get('GREAT_AGENT_RESEARCH_MODEL_API_KEY',
                                                   environ.get('GREAT_AGENT_MODEL_API_KEY', ''))
        settings = cls.model_validate(raw)
        settings.assert_safe_paths()
        return settings

    def assert_safe_paths(self) -> None:
        project_root = self.project_root.resolve()
        run_root = self.run_root.resolve()
        database_path = self.database_path.resolve()

        self._require_descendant(run_root, project_root, "run_root")
        self._require_descendant(database_path, project_root, "database_path")
        knowledge_root = (self.knowledge.root or project_root / 'data' / 'knowledge').resolve()
        self._require_descendant(knowledge_root, project_root, 'knowledge.root')
        protected = [run_root, database_path, *(project_root / name for name in
                     ('backend', 'frontend', 'config', '.git', '.codex', '.agents', '.venv', 'scripts'))]
        for directory in protected:
            directory = directory.resolve()
            if knowledge_root == directory or knowledge_root.is_relative_to(directory) or directory.is_relative_to(knowledge_root):
                raise ValueError('knowledge.root overlaps a protected application path')
        self.knowledge.root = knowledge_root
        import re
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', name) for name in self.research.code_roots):
            raise ValueError('research code source IDs must be simple identifiers')
        self.research.code_roots = {name: path.resolve() for name, path in
                                   (self.research.code_roots or {'project': project_root}).items()}
        if self.knowledge.docling_artifacts is not None:
            self.knowledge.docling_artifacts = self.knowledge.docling_artifacts.resolve()
        if self.knowledge.embedding_model_path is not None:
            self.knowledge.embedding_model_path = self.knowledge.embedding_model_path.resolve()
        if self.knowledge.embedding_tokenizer_path is not None:
            self.knowledge.embedding_tokenizer_path = self.knowledge.embedding_tokenizer_path.resolve()
        if self.mode == "local" and self.host != "127.0.0.1":
            raise ValueError("local mode requires host 127.0.0.1")

        self.project_root = project_root
        self.run_root = run_root
        self.database_path = database_path

    @staticmethod
    def _require_descendant(path: Path, root: Path, field_name: str) -> None:
        if path == root or not path.is_relative_to(root):
            raise ValueError(f"{field_name} must be inside project_root")

import math
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

Sensitivity = Literal["D0", "D1", "D2"]


class SafeError(Exception):
    """Only constant machine codes may leave the service boundary."""

    def __init__(self, code: str, retryable: bool = False):
        self.code, self.retryable = code, retryable
        super().__init__(code)


class OCROptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "throughput", "quality", "cpu_only", "gpu_only"] = "auto"
    sensitivity: Sensitivity = "D2"
    output: Literal["text", "markdown", "structured", "both"] = "both"
    language_hints: list[str] = Field(default_factory=lambda: ["de", "en"], max_length=10)
    layout: Literal["auto", "text", "blocks"] = "auto"
    preserve_boxes: bool = True
    extract_pdf_text_layer: bool = True
    force_ocr: bool = False
    prefer_cpu: bool = True
    allow_external_cpu: bool = True
    allow_gpu_overflow: bool = False
    max_pages: int = Field(default=100, ge=1, le=100)


class Block(BaseModel):
    type: str = "text"
    text: str = ""
    bbox_1000: list[float] | None = None
    polygon_1000: list[list[float]] | None = None
    confidence: float | None = None
    confidence_kind: str | None = None

    @field_validator("bbox_1000")
    @classmethod
    def validate_bbox(cls, box):
        if box is not None and (
            len(box) != 4
            or not all(math.isfinite(v) and 0 <= v <= 1000 for v in box)
            or box[0] > box[2]
            or box[1] > box[3]
        ):
            raise ValueError("invalid_bbox")
        return box

    @field_validator("polygon_1000")
    @classmethod
    def validate_polygon(cls, points):
        if points is not None and (
            len(points) != 4
            or any(
                len(p) != 2 or not all(math.isfinite(v) and 0 <= v <= 1000 for v in p)
                for p in points
            )
        ):
            raise ValueError("invalid_polygon")
        return points

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, value):
        if value is not None and not (math.isfinite(value) and 0 <= value <= 1):
            raise ValueError("invalid_confidence")
        return value


class Attempt(BaseModel):
    backend: str
    destination: str | None = None
    engine: str | None = None
    outcome: str
    latency_ms: float = 0
    queue_wait_ms: float = 0


class OCRPageResult(BaseModel):
    schema_version: str = "ocr-page/v1"
    page_index: int
    page_id: str = ""
    source: Literal["embedded_text", "cpu_ocr", "alias_ocr"]
    engine: str | None = None
    backend_version: str | None = None
    provider_destination: str | None = None
    text: str
    markdown: str
    image_width: int
    image_height: int
    rotation: int = 0
    transform: str = "source-raster; normalized 0..1000"
    blocks: list[Block] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)
    route_reason: str = ""
    latency_ms: float = 0
    attempt_count: int = 0
    attempts: list[Attempt] = Field(default_factory=list)
    alternatives: list["OCRPageResult"] = Field(default_factory=list)
    sensitivity: Sensitivity = "D2"


class BackendCapabilities(BaseModel):
    name: str
    text: bool = True
    bounding_boxes: bool = False
    layout_blocks: bool = False
    tables: bool = False
    math: bool = False
    languages: list[str] = Field(default_factory=list)
    max_image_px: int = 20_000_000
    supports_sensitive_classes: list[str] = Field(default_factory=list)
    effective_destination_verifiable: bool = False
    grounding_verified: bool = False
    multimodal_verified: bool = False


class BackendHealth(BaseModel):
    ready: bool
    code: str = "ok"


@dataclass
class RasterPage:
    index: int
    image: bytes
    width: int
    height: int
    page_id: str
    rotation: int = 0
    embedded: str | None = None
    features: dict = field(default_factory=dict)


class OCRBackend(Protocol):
    name: str
    destination: str | None
    external: bool

    async def capabilities(self) -> BackendCapabilities: ...
    async def health(self) -> BackendHealth: ...
    async def recognize(self, page: RasterPage, options: OCROptions) -> OCRPageResult: ...


class Failure(BaseModel):
    page_index: int
    code: str


class OCRResult(BaseModel):
    schema_version: str = "ocr-result/v1"
    job_id: str
    request_id: str
    status: str
    page_count: int
    sensitivity: Sensitivity
    markdown: str
    text: str
    pages: list[OCRPageResult]
    failures: list[Failure]
    anonymization_required: bool = True

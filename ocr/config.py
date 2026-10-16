import os
from dataclasses import dataclass
from urllib.parse import urlparse


def flag(name, default=False):
    return os.getenv(name, str(default)).lower() == "true"


@dataclass
class Settings:
    token: str = ""
    worker_token: str = ""
    cpu_url: str = ""
    freellm_url: str = ""
    freellm_key: str = ""
    gpu_enabled: bool = False
    overflow_enabled: bool = False
    overflow_qualified: bool = False
    dual_run: bool = False
    min_sensitivity: str = "D2"
    external_cpu_classes: tuple = ("D0",)
    max_upload: int = 25 * 1024 * 1024
    max_pages: int = 100
    max_pixels: int = 20_000_000
    total_pixels: int = 200_000_000
    cpu_workers: int = 1
    gpu_workers: int = 2
    queue_size: int = 32
    active_jobs: int = 4
    retained_jobs: int = 128
    max_result_bytes: int = 8 * 1024 * 1024
    retained_bytes: int = 64 * 1024 * 1024
    pages_per_job: int = 2
    ttl: float = 1800
    timeout: float = 90
    job_timeout: float = 900
    gpu_budget: int = 20
    rate_per_minute: int = 60
    models_dir: str = "/tmp/ocr-models"

    def __post_init__(self):
        if self.min_sensitivity not in ("D0", "D1", "D2"):
            raise ValueError("invalid_min_sensitivity")
        for n in (
            "max_upload",
            "max_pages",
            "max_pixels",
            "total_pixels",
            "cpu_workers",
            "gpu_workers",
            "queue_size",
            "active_jobs",
            "retained_jobs",
            "pages_per_job",
            "ttl",
            "timeout",
            "job_timeout",
            "gpu_budget",
            "rate_per_minute",
        ):
            if getattr(self, n) <= 0:
                raise ValueError("invalid_limit")
        if (
            self.max_upload > 25 * 1024 * 1024
            or self.max_pages > 100
            or self.max_pixels > 20_000_000
        ):
            raise ValueError("limit_exceeds_safety_ceiling")
        for url in (self.cpu_url, self.freellm_url):
            if not url:
                continue
            p = urlparse(url)
            if (
                p.username
                or p.password
                or p.query
                or p.fragment
                or p.scheme not in ("http", "https")
            ):
                raise ValueError("invalid_backend_url")
            if p.scheme == "http" and p.hostname not in (
                "localhost",
                "127.0.0.1",
                "::1",
            ):
                raise ValueError("remote_tls_required")

    @classmethod
    def from_env(cls):
        if os.getenv("OCR_GPU_MODEL", "alias-ocr") != "alias-ocr":
            raise ValueError("unsupported_gpu_model")
        return cls(
            token=os.getenv("OCR_DISPATCHER_TOKEN", ""),
            worker_token=os.getenv("OCR_CPU_AUTH_TOKEN", ""),
            cpu_url=os.getenv("OCR_CPU_URL", "").rstrip("/"),
            freellm_url=os.getenv("FREELLMAPI_BASE_URL", "").rstrip("/"),
            freellm_key=os.getenv("FREELLMAPI_API_KEY", ""),
            gpu_enabled=flag("OCR_ENABLE_GPU"),
            overflow_enabled=flag("OCR_ENABLE_GPU_OVERFLOW"),
            overflow_qualified=flag("OCR_OVERFLOW_BENCHMARK_QUALIFIED"),
            dual_run=flag("OCR_QUALITY_DUAL_RUN"),
            min_sensitivity=os.getenv("OCR_MIN_SENSITIVITY", "D2"),
            external_cpu_classes=tuple(
                c for c in ("D0", "D1", "D2") if flag("OCR_ALLOW_EXTERNAL_CPU_" + c, c == "D0")
            ),
            max_upload=int(os.getenv("OCR_MAX_UPLOAD_MIB", "25")) * 1024 * 1024,
            max_pages=int(os.getenv("OCR_MAX_PAGES", "100")),
            total_pixels=int(os.getenv("OCR_MAX_TOTAL_PIXELS", "200000000")),
            queue_size=int(os.getenv("OCR_BACKEND_QUEUE_CAPACITY", "32")),
            active_jobs=int(os.getenv("OCR_MAX_ACTIVE_JOBS", "4")),
            retained_jobs=int(os.getenv("OCR_MAX_RETAINED_JOBS", "128")),
            timeout=float(os.getenv("OCR_BACKEND_TIMEOUT_SECONDS", "90")),
            job_timeout=float(os.getenv("OCR_JOB_TIMEOUT_SECONDS", "900")),
            gpu_budget=int(os.getenv("OCR_GPU_REQUESTS_PER_MINUTE", "20")),
            max_pixels=int(os.getenv("OCR_MAX_PIXELS", "20000000")),
            cpu_workers=int(os.getenv("OCR_CPU_CONCURRENCY", "1")),
            gpu_workers=int(os.getenv("OCR_GPU_CONCURRENCY", "2")),
            ttl=float(os.getenv("OCR_JOB_TTL_MIN", "30")) * 60,
            models_dir=os.getenv("OCR_MODELS_DIR", "/tmp/ocr-models"),
        )

"""Central configuration. Everything is overridable through environment variables."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Tiny .env loader (no extra dependency). Real environment variables always win."""
    f = BASE_DIR / ".env"
    if not f.exists():
        return
    for line in f.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split(" #", 1)[0].strip().strip('"').strip("'")
        if v:                                   # an empty value means "not set"
            os.environ.setdefault(k.strip(), v)


_load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(BASE_DIR / "data"))))
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", ""))
    provider: str = field(default_factory=lambda: _env("PROVIDER", "mock"))  # mock | anthropic
    anthropic_model: str = field(default_factory=lambda: _env("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    verify_mode: str = field(default_factory=lambda: _env("VERIFY_MODE", "second_pass"))  # second_pass | none
    mock_truth_dir: Path = field(default_factory=lambda: Path(_env("MOCK_TRUTH_DIR", str(BASE_DIR / "data" / "eval_set"))))

    # limits
    max_file_bytes: int = int(_env("MAX_FILE_BYTES", str(8 * 1024 * 1024)))
    max_files_per_upload: int = int(_env("MAX_FILES_PER_UPLOAD", "10"))
    max_pdf_pages: int = int(_env("MAX_PDF_PAGES", "3"))
    invoice_timeout_s: float = float(_env("INVOICE_TIMEOUT_S", "90"))
    model_call_timeout_s: float = float(_env("MODEL_CALL_TIMEOUT_S", "45"))
    daily_model_call_cap: int = int(_env("DAILY_MODEL_CALL_CAP", "300"))
    uploads_per_visitor_per_hour: int = int(_env("UPLOADS_PER_VISITOR_PER_HOUR", "30"))
    uploads_per_ip_per_hour: int = int(_env("UPLOADS_PER_IP_PER_HOUR", "60"))
    retry_backoff_s: float = float(_env("RETRY_BACKOFF_S", "0.5"))
    max_repair_attempts: int = int(_env("MAX_REPAIR_ATTEMPTS", "2"))
    worker_threads: int = int(_env("WORKER_THREADS", "3"))

    # retention / privacy
    visitor_ttl_hours: float = float(_env("VISITOR_TTL_HOURS", "24"))
    file_ttl_hours: float = float(_env("FILE_TTL_HOURS", "2"))
    trust_proxy: bool = _env("TRUST_PROXY", "0") == "1"
    cookie_secure: bool = _env("COOKIE_SECURE", "0") == "1"

    # business rules (configurable, NOT hard-coded in validators)
    vat_rate: float = float(_env("VAT_RATE", "0.10"))
    economic_code_lengths: tuple = (12, 14)
    low_confidence_threshold: float = float(_env("LOW_CONFIDENCE", "0.75"))
    max_issue_future_days: int = 30
    rounding_tol_per_row: int = int(_env("ROUNDING_TOL_PER_ROW", "1"))

    @property
    def db_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'app.db'}"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def samples_dir(self) -> Path:
        return self.data_dir / "samples"


settings = Settings()
for _d in (settings.data_dir, settings.upload_dir, settings.samples_dir):
    _d.mkdir(parents=True, exist_ok=True)

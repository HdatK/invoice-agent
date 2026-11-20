"""Model-independent extraction interface. The pipeline only ever talks to this."""
from __future__ import annotations
from typing import Protocol


class ProviderError(Exception):
    """Transient or permanent model failure (network, rate limit, unparsable output)."""

    def __init__(self, msg: str, retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable


class ExtractionProvider(Protocol):
    name: str
    model: str | None

    def extract(self, images: list[bytes], text: str | None, *, hints: list[str] | None = None,
                variant: int = 1, timeout: float = 45.0, source_sha: str | None = None) -> dict:
        """Return a raw extraction dict (see pipeline.schema). `variant` 2 = independent second read."""
        ...

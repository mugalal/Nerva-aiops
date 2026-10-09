"""Local provider types; M3 does not need another service's Python package."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProviderErrorCategory(StrEnum):
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"
    NOT_FOUND = "not_found"
    INSUFFICIENT_DATA = "insufficient_data"


class ProviderError(Exception):
    def __init__(self, category, message, provider, retryable=False):
        super().__init__(message)
        self.category = category
        self.message = message
        self.provider = provider
        self.retryable = retryable
        self.status_code = (404 if category == ProviderErrorCategory.NOT_FOUND else
                            503 if category in {ProviderErrorCategory.UNAVAILABLE,
                                                ProviderErrorCategory.TIMEOUT} else 502)

    def __str__(self):
        return f"[{self.category}] {self.provider}: {self.message}"


@dataclass(frozen=True)
class ProviderResponse:
    data: Any
    provider: str
    mode: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseProvider(ABC):
    @abstractmethod
    async def fetch(self, incident_id: str) -> ProviderResponse:
        raise NotImplementedError

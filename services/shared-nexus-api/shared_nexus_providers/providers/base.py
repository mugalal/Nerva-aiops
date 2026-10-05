from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ProviderResponse(Generic[T]):
    """
    Standard response returned by every NEXUS provider.

    The consumer does not need to know whether the data
    came from a mock provider or a real provider.
    """

    data: T | None
    provider: str
    mode: str
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseProvider(ABC, Generic[T]):
    """
    Common interface for all NEXUS providers.

    Business logic depends on this interface rather than
    directly depending on HTTP, files, Prometheus, Kubernetes, etc.
    """

    name: str = "base"
    mode: str = "mock"

    @abstractmethod
    async def fetch(self, incident_id: str) -> ProviderResponse[T]:
        """
        Fetch evidence related to an incident.
        """
        raise NotImplementedError
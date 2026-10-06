from enum import StrEnum


class ProviderErrorCategory(StrEnum):
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    INSUFFICIENT_DATA = "insufficient_data"
    INVALID_RESPONSE = "invalid_response"


class ProviderError(Exception):
    """
    Standard error used by all NEXUS providers.
    """

    def __init__(
        self,
        category: ProviderErrorCategory,
        message: str,
        provider: str,
        retryable: bool = False,
    ):
        super().__init__(message)

        self.category = category
        self.message = message
        self.provider = provider
        self.retryable = retryable

    def __str__(self) -> str:
        return (
            f"[{self.category}] "
            f"{self.provider}: "
            f"{self.message}"
        )
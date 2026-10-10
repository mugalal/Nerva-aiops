class M1Error(Exception):
    def __init__(
        self,
        category: str,
        message: str,
        *,
        status_code: int = 503,
        retryable: bool = True,
    ):
        super().__init__(message)
        self.category = category
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


class ProviderUnavailable(M1Error):
    def __init__(self, provider: str, message: str):
        super().__init__(
            "provider_unavailable",
            f"{provider}: {message}",
            status_code=503,
            retryable=True,
        )


class ProviderInvalidResponse(M1Error):
    def __init__(self, provider: str, message: str):
        super().__init__(
            "invalid_provider_response",
            f"{provider}: {message}",
            status_code=502,
            retryable=True,
        )


class TelemetryMissing(M1Error):
    def __init__(self, message: str):
        super().__init__("telemetry_missing", message, status_code=503, retryable=True)


class TelemetryStale(M1Error):
    def __init__(self, message: str):
        super().__init__("telemetry_stale", message, status_code=503, retryable=True)


class EvidenceNotFound(M1Error):
    def __init__(self, incident_id: str):
        super().__init__(
            "evidence_not_found",
            f"No M1 evidence is stored for incident {incident_id}",
            status_code=404,
            retryable=False,
        )


class BaselineNotFound(M1Error):
    def __init__(self, service: str):
        super().__init__(
            "baseline_not_found",
            f"No measured healthy baseline is stored for service {service}",
            status_code=409,
            retryable=False,
        )


class StorageUnavailable(M1Error):
    def __init__(self):
        super().__init__("storage_unavailable", "M1 persistent evidence storage is not readable or writable",
                         status_code=503, retryable=True)

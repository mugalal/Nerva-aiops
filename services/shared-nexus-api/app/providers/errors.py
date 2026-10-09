from fastapi import HTTPException


class IntegrationError(ValueError):
    def __init__(self, message: str, *, status_code: int = 503, retryable: bool = True):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class RecoveryPending(IntegrationError):
    def __init__(self, message: str = "M1 is collecting post-action recovery evidence"):
        super().__init__(message, status_code=202, retryable=True)


def as_http_error(error: ValueError) -> HTTPException:
    if isinstance(error, IntegrationError):
        return HTTPException(
            status_code=error.status_code,
            detail={"category": "recovery_pending" if isinstance(error, RecoveryPending) else "dependency_error",
                    "message": str(error), "retryable": error.retryable},
            headers={"Retry-After": "15"} if error.retryable else None,
        )
    return HTTPException(status_code=400, detail=str(error))

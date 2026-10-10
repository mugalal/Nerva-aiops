import re
from fastapi import HTTPException


def sanitize_message(msg: str) -> str:
    """Sanitize error messages: strip newlines, redact internal paths, and truncate."""
    if not isinstance(msg, str):
        msg = str(msg)
    first_line = msg.split("\n")[0].strip()
    sanitized = re.sub(r"[a-zA-Z]:\\[\w\\\.-]+|/(?:[\w\.-]+/)+[\w\.-]+", "<internal-path>", first_line)
    if len(sanitized) > 200:
        sanitized = sanitized[:197] + "..."
    return sanitized


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
        clean_msg = sanitize_message(str(error))
        return HTTPException(
            status_code=error.status_code,
            detail={"category": "recovery_pending" if isinstance(error, RecoveryPending) else "dependency_error",
                    "message": clean_msg, "retryable": error.retryable},
            headers={"Retry-After": "15"} if error.retryable else None,
        )
    return HTTPException(status_code=400, detail=sanitize_message(str(error)))


from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from ..errors import ProviderInvalidResponse, ProviderUnavailable


@dataclass(frozen=True)
class LogEntry:
    timestamp: datetime
    labels: dict[str, str]
    line: str


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


class LokiClient:
    name = "loki"

    def __init__(self, base_url: str, timeout: float = 5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                return response
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable(self.name, f"request timed out: {url}") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                raise ProviderUnavailable(
                    self.name, f"HTTP {exc.response.status_code} from {url}"
                ) from exc
            raise ProviderInvalidResponse(
                self.name, f"HTTP {exc.response.status_code} from {url}"
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailable(self.name, str(exc)) from exc

    async def ready(self) -> tuple[bool, str]:
        try:
            response = await self._get("/ready")
            return True, response.text.strip() or "ready"
        except (ProviderUnavailable, ProviderInvalidResponse) as exc:
            return False, exc.message

    async def query_logs(
        self,
        service: str,
        *,
        start: datetime,
        end: datetime,
        limit: int = 100,
    ) -> list[LogEntry]:
        query = f'{{service_name="{_escape_label(service)}"}}'
        response = await self._get(
            "/loki/api/v1/query_range",
            {
                "query": query,
                "start": str(int(start.timestamp() * 1_000_000_000)),
                "end": str(int(end.timestamp() * 1_000_000_000)),
                "limit": limit,
                "direction": "backward",
            },
        )
        try:
            payload = response.json()
            if payload.get("status") != "success":
                raise ValueError(payload.get("error", "log query failed"))
            entries = []
            for stream in payload["data"]["result"]:
                labels = {str(k): str(v) for k, v in stream.get("stream", {}).items()}
                for timestamp_ns, line in stream.get("values", []):
                    entries.append(
                        LogEntry(
                            timestamp=datetime.fromtimestamp(
                                int(timestamp_ns) / 1_000_000_000, tz=timezone.utc
                            ),
                            labels=labels,
                            line=str(line),
                        )
                    )
            return sorted(entries, key=lambda entry: entry.timestamp)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(self.name, str(exc)) from exc

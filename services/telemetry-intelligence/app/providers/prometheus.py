from dataclasses import dataclass
import math
from typing import Any

import httpx

from ..errors import ProviderInvalidResponse, ProviderUnavailable, TelemetryMissing


@dataclass(frozen=True)
class PrometheusSample:
    labels: dict[str, str]
    timestamp: float
    value: float


@dataclass(frozen=True)
class PrometheusSeries:
    labels: dict[str, str]
    values: list[tuple[float, float]]


class PrometheusClient:
    name = "prometheus"

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
            response = await self._get("/-/ready")
            detail = response.text.strip() or "ready"
            return True, detail
        except (ProviderUnavailable, ProviderInvalidResponse) as exc:
            return False, exc.message

    async def query_vector(
        self, query: str, *, evaluation_time: float | None = None
    ) -> list[PrometheusSample]:
        params: dict[str, Any] = {"query": query}
        if evaluation_time is not None:
            params["time"] = evaluation_time
        response = await self._get("/api/v1/query", params)
        try:
            payload = response.json()
            if payload.get("status") != "success":
                raise ValueError(payload.get("error", "query failed"))
            data = payload["data"]
            if data["resultType"] != "vector":
                raise ValueError(f"expected vector, received {data['resultType']}")
            samples = []
            for item in data["result"]:
                timestamp, raw_value = item["value"]
                value = float(raw_value)
                timestamp = float(timestamp)
                if not math.isfinite(timestamp):
                    raise ValueError("sample timestamp must be finite")
                if not math.isfinite(value):
                    continue
                samples.append(
                    PrometheusSample(
                        labels={str(k): str(v) for k, v in item.get("metric", {}).items()},
                        timestamp=timestamp,
                        value=value,
                    )
                )
            return samples
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(self.name, str(exc)) from exc

    async def query_scalar(
        self, query: str, *, evaluation_time: float | None = None
    ) -> PrometheusSample:
        samples = await self.query_vector(query, evaluation_time=evaluation_time)
        if not samples:
            raise TelemetryMissing(f"Prometheus returned no samples for query: {query}")
        if len(samples) > 1:
            raise ProviderInvalidResponse(
                self.name, f"expected one aggregate sample, received {len(samples)}"
            )
        return samples[0]

    async def query_range(
        self,
        query: str,
        *,
        start: float,
        end: float,
        step_seconds: int,
    ) -> list[PrometheusSeries]:
        response = await self._get(
            "/api/v1/query_range",
            {"query": query, "start": start, "end": end, "step": step_seconds},
        )
        try:
            payload = response.json()
            if payload.get("status") != "success":
                raise ValueError(payload.get("error", "range query failed"))
            data = payload["data"]
            if data["resultType"] != "matrix":
                raise ValueError(f"expected matrix, received {data['resultType']}")
            series = []
            for item in data["result"]:
                values = []
                for timestamp, raw_value in item.get("values", []):
                    value = float(raw_value)
                    timestamp = float(timestamp)
                    if not math.isfinite(timestamp):
                        raise ValueError("sample timestamp must be finite")
                    if math.isfinite(value):
                        values.append((timestamp, value))
                series.append(
                    PrometheusSeries(
                        labels={str(k): str(v) for k, v in item.get("metric", {}).items()},
                        values=values,
                    )
                )
            return series
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderInvalidResponse(self.name, str(exc)) from exc

    async def query_range_scalar(
        self,
        query: str,
        *,
        start: float,
        end: float,
        step_seconds: int,
    ) -> list[tuple[float, float]]:
        series = await self.query_range(
            query, start=start, end=end, step_seconds=step_seconds
        )
        if not series or not any(item.values for item in series):
            raise TelemetryMissing(f"Prometheus returned no range data for query: {query}")
        if len(series) > 1:
            raise ProviderInvalidResponse(
                self.name, f"expected one aggregate series, received {len(series)}"
            )
        return series[0].values

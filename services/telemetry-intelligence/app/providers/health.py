import httpx

from ..errors import ProviderInvalidResponse, ProviderUnavailable


class ServiceHealthClient:
    name = "service_health"

    def __init__(self, service_base_urls: dict[str, str], timeout: float = 5.0):
        self.service_base_urls = {
            name: url.rstrip("/") for name, url in service_base_urls.items()
        }
        self.timeout = timeout

    async def check(self, service: str) -> tuple[bool, str]:
        base_url = self.service_base_urls.get(service)
        if not base_url:
            raise ProviderInvalidResponse(
                self.name, f"no health URL is configured for service {service}"
            )
        url = f"{base_url}/health"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json()
        except httpx.TimeoutException as exc:
            raise ProviderUnavailable(self.name, f"request timed out: {url}") from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailable(self.name, str(exc)) from exc
        except httpx.HTTPStatusError as exc:
            raise ProviderUnavailable(
                self.name, f"HTTP {exc.response.status_code} from {url}"
            ) from exc
        except ValueError as exc:
            raise ProviderInvalidResponse(self.name, str(exc)) from exc

        status = str(payload.get("status", "")).lower()
        return status == "ok", f"status={status or 'missing'}"

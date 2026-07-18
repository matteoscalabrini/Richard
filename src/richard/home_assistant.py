from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from richard.errors import HomeAssistantError


@dataclass(frozen=True)
class HomeAssistantEntity:
    entity_id: str
    state: str
    attributes: dict = field(default_factory=dict)
    last_changed: str | None = None
    last_updated: str | None = None

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def name(self) -> str:
        return str(self.attributes.get("friendly_name") or self.entity_id)

    @classmethod
    def from_payload(cls, payload: object) -> "HomeAssistantEntity":
        if not isinstance(payload, dict) or not payload.get("entity_id"):
            raise HomeAssistantError("Home Assistant returned an invalid entity")
        attributes = payload.get("attributes")
        last_changed = payload.get("last_changed")
        last_updated = payload.get("last_updated")
        return cls(
            entity_id=str(payload["entity_id"]),
            state=str(payload.get("state", "unknown")),
            attributes=attributes if isinstance(attributes, dict) else {},
            last_changed=str(last_changed) if last_changed else None,
            last_updated=str(last_updated) if last_updated else None,
        )


class HomeAssistantClient:
    """Small client for Home Assistant's bearer-authenticated REST API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 10.0,
        verify_ssl: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        base = base_url.strip().rstrip("/")
        if base.endswith("/api"):
            base = base[: -len("/api")]
        if not base:
            raise ValueError("Home Assistant URL is required")
        if not token:
            raise ValueError("Home Assistant token is required")
        self._base_url = base
        self._token = token
        self._client = client or httpx.Client(timeout=timeout, verify=verify_ssl)

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def list_entities(self) -> list[HomeAssistantEntity]:
        payload = self._request("GET", "/api/states")
        if not isinstance(payload, list):
            raise HomeAssistantError("Home Assistant returned an invalid states response")
        return [HomeAssistantEntity.from_payload(item) for item in payload]

    def get_entity(self, entity_id: str) -> HomeAssistantEntity:
        payload = self._request("GET", f"/api/states/{entity_id}")
        return HomeAssistantEntity.from_payload(payload)

    def call_service(self, domain: str, service: str, data: dict) -> list[HomeAssistantEntity]:
        payload = self._request("POST", f"/api/services/{domain}/{service}", json=data)
        # Ordinary service calls return the states changed during execution. Some
        # installations/actions return an empty body or a response object instead.
        if not isinstance(payload, list):
            return []
        return [HomeAssistantEntity.from_payload(item) for item in payload]

    def _request(self, method: str, path: str, **kwargs):
        try:
            response = self._client.request(
                method, self._base_url + path, headers=self._headers, **kwargs
            )
            response.raise_for_status()
            if not response.content:
                return None
            return response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 401:
                message = "Home Assistant rejected the access token"
            elif status == 404:
                message = "Home Assistant entity or service was not found"
            else:
                message = f"Home Assistant returned HTTP {status}"
            raise HomeAssistantError(message) from exc
        except httpx.HTTPError as exc:
            raise HomeAssistantError(f"Home Assistant is unreachable: {exc}") from exc
        except ValueError as exc:
            raise HomeAssistantError("Home Assistant returned invalid JSON") from exc

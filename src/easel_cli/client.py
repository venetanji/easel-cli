"""HTTP client for Easel's OpenAI-compatible endpoints."""

from __future__ import annotations

import base64
import binascii
from typing import Any

import httpx


class EaselError(Exception):
    """An HTTP or response-format error returned by Easel."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class EaselClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 600,
        http_client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._http = http_client or httpx.Client(timeout=timeout)
        self._owns_http = http_client is None
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "EaselClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _get_json(self, path: str) -> dict[str, Any]:
        return self._decode_response(self._http.get(
            f"{self.base_url}{path}", headers=self._headers
        ))

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._decode_response(self._http.post(
            f"{self.base_url}{path}", headers=self._headers, json=payload
        ))

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = f"Easel API returned HTTP {response.status_code}"
            try:
                message = response.json().get("error", {}).get("message") or message
            except (ValueError, AttributeError):
                pass
            raise EaselError(message, response.status_code) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise EaselError("Easel API returned invalid JSON", response.status_code) from exc
        if not isinstance(body, dict):
            raise EaselError("Easel API returned an unexpected response", response.status_code)
        return body

    def list_models(self) -> list[str]:
        body = self._get_json("/v1/models")
        try:
            return [item["id"] for item in body["data"]]
        except (KeyError, TypeError) as exc:
            raise EaselError("Easel API returned an invalid models response") from exc

    def generate(
        self,
        prompt: str,
        *,
        model: str = "flux2-9b",
        size: str | None = None,
        n: int = 1,
        steps: int | None = None,
        seed: int | None = None,
    ) -> list[bytes]:
        payload: dict[str, Any] = {"prompt": prompt, "model": model, "n": n}
        for name, value in (("size", size), ("steps", steps), ("seed", seed)):
            if value is not None:
                payload[name] = value
        body = self._post_json("/v1/images/generations", payload)
        try:
            data = body["data"]
            if not isinstance(data, list) or not data:
                raise ValueError
            return [base64.b64decode(item["b64_json"], validate=True) for item in data]
        except (KeyError, TypeError, ValueError, binascii.Error) as exc:
            raise EaselError("Easel API response did not contain b64_json image data") from exc

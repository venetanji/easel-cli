"""Submit-once HTTP client for Easel images and video jobs."""
from __future__ import annotations

import base64
import binascii
import math
import mimetypes
import re
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

IMAGE_MODEL = "qwen-image-2.1"
VIDEO_MODEL = "ltx-2.5"
JOB_STATUSES = {"queued", "in_progress", "completed", "failed", "cancelled"}


class EaselError(Exception):
    """API, transport or response error with structured server metadata."""

    def __init__(self, message: str, status_code: int | None = None, *,
                 code: str | None = None, param: str | None = None,
                 retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.param = param
        self.retry_after = retry_after


def _positive(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise EaselError(f"{name} must be a finite positive number")


class EaselClient:
    def __init__(self, base_url: str, api_key: str, *, timeout: float = 600,
                 http_client: httpx.Client | None = None,
                 max_download_bytes: int = 512 * 1024 * 1024):
        _positive(timeout, "timeout")
        _positive(max_download_bytes, "max_download_bytes")
        parsed = urlsplit(base_url)
        local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not parsed.hostname or (parsed.scheme != "https" and not local_http)
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise EaselError("Easel base URL must use HTTPS (HTTP allowed only on loopback), without credentials, query or fragment")
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self._http = http_client or httpx.Client(timeout=timeout)
        self._owns_http = http_client is None
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._api_key = api_key
        self.timeout = timeout
        self.max_download_bytes = max_download_bytes

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> EaselClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "[REDACTED]") if self._api_key else text

    def _check_response(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        message = f"Easel API returned HTTP {response.status_code}"
        error: dict[str, Any] = {}
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                error = body["error"]
                if isinstance(error.get("message"), str):
                    message = error["message"]
        except ValueError:
            pass
        retry_after = None
        try:
            delay = float(response.headers.get("Retry-After", ""))
            if math.isfinite(delay) and delay >= 0:
                retry_after = delay
        except ValueError:
            pass
        raise EaselError(self._redact(message), response.status_code,
                         code=self._redact(error["code"]) if isinstance(error.get("code"), str) else None,
                         param=self._redact(error["param"]) if isinstance(error.get("param"), str) else None,
                         retry_after=retry_after)

    def _decode_response(self, response: httpx.Response) -> dict[str, Any]:
        self._check_response(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise EaselError("Easel API returned invalid JSON", response.status_code) from exc
        if not isinstance(body, dict):
            raise EaselError("Easel API returned an unexpected response", response.status_code)
        return body

    def _get_json(self, path: str, *, timeout: float | None = None) -> dict[str, Any]:
        return self._decode_response(self._http.get(
            f"{self.base_url}{path}", headers=self._headers, follow_redirects=False,
            timeout=self.timeout if timeout is None else timeout))

    def _submit(self, path: str, *, async_mode: bool = False, **kwargs: Any) -> dict[str, Any]:
        headers = dict(self._headers)
        if async_mode:
            headers["Prefer"] = "respond-async"
        try:
            response = self._http.post(f"{self.base_url}{path}", headers=headers,
                                       follow_redirects=False, timeout=self.timeout, **kwargs)
        except httpx.HTTPError as exc:
            # Once a POST starts, loss of the receipt cannot prove rejection.
            raise EaselError("Submission outcome is ambiguous; do not automatically resubmit. "
                             "The server has no idempotency or receipt-recovery endpoint.") from exc
        try:
            return self._decode_response(response)
        except EaselError as exc:
            if response.is_success:
                raise EaselError("Submission outcome is ambiguous: invalid response; do not resubmit. "
                                 "The server has no receipt-recovery endpoint.") from exc
            raise

    def list_models(self) -> list[str]:
        body = self._get_json("/v1/models")
        try:
            data = body["data"]
            if not isinstance(data, list) or any(not isinstance(item.get("id"), str) for item in data):
                raise ValueError
            return [item["id"] for item in data]
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise EaselError("Easel API returned an invalid models response") from exc

    def list_loras(self) -> list[dict[str, Any]]:
        data = self._get_json("/v1/videos/loras").get("data")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise EaselError("Easel API returned an invalid LoRA catalog")
        return data

    @staticmethod
    def _upload(stack: ExitStack, path: Path) -> tuple[str, Any, str]:
        path = Path(path)
        mime = mimetypes.guess_type(path.name)[0]
        if mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise EaselError("Reference files must be PNG, JPEG or WebP")
        return path.name, stack.enter_context(path.open("rb")), mime

    def image_request(self, operation: str, *, prompt: str | None = None,
                      images: list[Path] | None = None, async_mode: bool = False,
                      model: str = IMAGE_MODEL, size: str | None = None, n: int = 1,
                      steps: int | None = None, seed: int | None = None,
                      server: str | None = None, response_format: str | None = None) -> dict[str, Any]:
        if operation not in {"generations", "edits", "variations"}:
            raise EaselError("Unknown image operation")
        payload: dict[str, Any] = {"model": model, "n": n}
        for name, value in (("prompt", prompt), ("size", size), ("steps", steps),
                            ("seed", seed), ("server", server), ("response_format", response_format)):
            if value is not None:
                payload[name] = value
        path = f"/v1/images/{operation}"
        if operation == "generations":
            body = self._submit(path, json=payload, async_mode=async_mode)
        else:
            if not images:
                raise EaselError("At least one reference image is required")
            with ExitStack() as stack:
                files = [("image[]", self._upload(stack, image)) for image in images]
                body = self._submit(path, data=payload, files=files, async_mode=async_mode)
        if async_mode:
            self._validate_receipt(body, "image")
        return body

    def generate(self, prompt: str, *, model: str = IMAGE_MODEL, size: str | None = None,
                 n: int = 1, steps: int | None = None, seed: int | None = None,
                 server: str | None = None) -> list[bytes]:
        body = self.image_request("generations", prompt=prompt, model=model, size=size,
                                  n=n, steps=steps, seed=seed, server=server)
        return self.images_from_response(body)

    def submit_image(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("response_format", "url")
        return self.image_request("generations", prompt=prompt, async_mode=True, **kwargs)

    def create_video(self, prompt: str, *, model: str = VIDEO_MODEL, seconds: int = 4,
                     size: str = "1280x720", input_reference: Path | None = None,
                     camera_lora: str | None = None, camera_lora_strength: float | None = None,
                     loras: str | None = None, motion_speed: float | None = None,
                     seed: int | None = None, lora_reference: Path | None = None,
                     lora_reference_strength: float | None = None) -> dict[str, Any]:
        fields = {"prompt": prompt, "model": model, "seconds": seconds, "size": size,
                  "camera_lora": camera_lora, "camera_lora_strength": camera_lora_strength,
                  "loras": loras, "motion_speed": motion_speed, "seed": seed,
                  "lora_reference_strength": lora_reference_strength}
        # Non-file multipart tuples force multipart even for text-to-video requests.
        files = [(name, (None, str(value))) for name, value in fields.items() if value is not None]
        with ExitStack() as stack:
            for name, path in (("input_reference", input_reference), ("lora_reference", lora_reference)):
                if path is not None:
                    files.append((name, self._upload(stack, path)))
            body = self._submit("/v1/videos", files=files)
        self._validate_receipt(body, "video")
        return body

    @staticmethod
    def _job_path(kind: str, job_id: str) -> str:
        prefix = {"image": "image_job_", "video": "video_"}.get(kind)
        if prefix is None or not isinstance(job_id, str) or not job_id.startswith(prefix) or not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id):
            raise EaselError("Invalid job ID or job kind")
        return f"/v1/images/jobs/{job_id}" if kind == "image" else f"/v1/videos/{job_id}"

    def _validate_job(self, body: dict[str, Any], kind: str, job_id: str | None = None) -> None:
        self._job_path(kind, body.get("id"))
        if not isinstance(body.get("status"), str) or body["status"] not in JOB_STATUSES or (job_id and body["id"] != job_id):
            raise EaselError("Easel API returned an invalid job receipt/status")

    def _validate_receipt(self, body: dict[str, Any], kind: str) -> None:
        try:
            self._validate_job(body, kind)
        except EaselError as exc:
            known_id = body.get("id")
            try:
                self._job_path(kind, known_id)
            except EaselError:
                known_id = None
            detail = f" for {known_id}" if known_id else ""
            raise EaselError(self._redact(f"Submission outcome is ambiguous: invalid receipt{detail}; do not resubmit. "
                             "Try status with the known ID; the server has no receipt-recovery endpoint.")) from exc

    def get_job(self, kind: str, job_id: str, *, timeout: float | None = None) -> dict[str, Any]:
        body = self._get_json(self._job_path(kind, job_id), timeout=timeout)
        self._validate_job(body, kind, job_id)
        return body

    def video_queue(self, job_id: str) -> dict[str, Any]:
        self._job_path("video", job_id)
        return self._get_json(f"/v1/videos/queue/{job_id}")

    def wait_for_job(self, kind: str, job_id: str, *, timeout: float = 600,
                     poll_interval: float = 2) -> dict[str, Any]:
        _positive(timeout, "wait timeout")
        _positive(poll_interval, "poll interval")
        self._job_path(kind, job_id)
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            delay = poll_interval
            try:
                body = self.get_job(kind, job_id, timeout=min(self.timeout, remaining))
            except EaselError as exc:
                if exc.status_code not in {429, 502, 503, 504}:
                    raise EaselError(f"{kind} {job_id}: {exc}", exc.status_code, code=exc.code,
                                     param=exc.param, retry_after=exc.retry_after) from exc
                delay = max(poll_interval, exc.retry_after or 0)
            except httpx.TransportError:
                pass  # Safe GET retry only; the original ID is never submitted again.
            else:
                if body["status"] == "completed":
                    return body
                if body["status"] in {"failed", "cancelled"}:
                    error = body.get("error") or {}
                    message = error.get("message", body["status"]) if isinstance(error, dict) else body["status"]
                    raise EaselError(self._redact(f"{kind} {job_id} {body['status']}: {message}"),
                                     code=self._redact(error["code"]) if isinstance(error, dict) and isinstance(error.get("code"), str) else None)
            time.sleep(min(delay, max(0, deadline - time.monotonic())))
        raise EaselError(f"Timed out waiting for {kind} {job_id}; resume monitoring the same ID. "
                         "The job may still be running; do not resubmit.")

    def _download(self, url: str) -> bytes:
        with self._http.stream("GET", url, headers=self._headers, follow_redirects=False,
                               timeout=self.timeout) as response:
            raw = bytearray()
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                if len(raw) + len(chunk) > self.max_download_bytes:
                    raise EaselError(f"Download exceeds {self.max_download_bytes} byte limit")
                raw.extend(chunk)
            if not response.is_success:
                self._check_response(httpx.Response(response.status_code, content=bytes(raw),
                    headers={"Retry-After": response.headers.get("Retry-After", "")}))
            return bytes(raw)

    def _image_url(self, value: Any) -> str:
        if not isinstance(value, str):
            raise EaselError("Invalid image download URL")
        url = urljoin(self.base_url + "/", value)
        base, parsed = urlsplit(self.base_url), urlsplit(url)
        origin = lambda u: (u.scheme, u.hostname, u.port or (443 if u.scheme == "https" else 80))
        allowed = parsed.path == f"{base.path}/v1/images/view" or re.fullmatch(
            re.escape(base.path) + r"/v1/images/jobs/image_job_[A-Za-z0-9_-]+/content/[0-9]+", parsed.path)
        if origin(parsed) != origin(base) or parsed.username or parsed.password or parsed.fragment or not allowed:
            raise EaselError("Refusing untrusted image download URL")
        return url

    def images_from_response(self, body: dict[str, Any]) -> list[bytes]:
        data = body.get("data")
        if not isinstance(data, list) or not 1 <= len(data) <= 16:
            raise EaselError("Easel API response did not contain b64_json image data or image URLs (1–16 outputs)")
        result = []
        total = 0
        for item in data:
            try:
                if "b64_json" in item:
                    encoded = item["b64_json"]
                    if len(encoded) > 4 * ((self.max_download_bytes + 2) // 3):
                        raise EaselError(f"Image data exceeds {self.max_download_bytes} byte limit")
                    image = base64.b64decode(encoded, validate=True)
                elif "url" in item:
                    image = self._download(self._image_url(item["url"]))
                else:
                    raise ValueError
            except (KeyError, TypeError, ValueError, binascii.Error) as exc:
                raise EaselError("Easel API response did not contain b64_json image data or image URLs") from exc
            total += len(image)
            if total > self.max_download_bytes:
                raise EaselError(f"Image data exceeds {self.max_download_bytes} byte limit")
            result.append(image)
        return result

    def download_video(self, job_id: str) -> bytes:
        return self._download(f"{self.base_url}{self._job_path('video', job_id)}/content")

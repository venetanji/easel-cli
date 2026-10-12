"""Submit-once HTTP client for Easel images and video jobs."""
from __future__ import annotations

import base64
import binascii
import json
import math
import mimetypes
import os
import re
import stat
import time
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict, cast
from urllib.parse import urljoin, urlsplit

import httpx

IMAGE_MODEL = "qwen-image-2.1"
VIDEO_MODEL = "ltx-2.5"
JOB_STATUSES = {"queued", "in_progress", "completed", "failed", "cancelled"}
VIDEO_SIZES = ("512x320", "640x384", "768x512", "1280x720", "720x1280", "1792x1024", "1024x1792")
MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MAX_AUDIO_BYTES = 32 * 1024 * 1024
# Canonical MIME for each supported file extension; server validates actual bytes.
AUDIO_FILE_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac",
                    ".ogg": "audio/ogg", ".m4a": "audio/mp4"}
CAMERA_LORAS = ("dolly-in", "dolly-out", "dolly-left", "dolly-right", "jib-up", "jib-down", "static")


@dataclass(frozen=True)
class LoRASelection:
    """Curated adapter ID and finite 0–2 model strength."""
    id: str
    strength: float = 1.0


@dataclass(frozen=True)
class GuideFrame:
    """Local still image at an explicit pixel-frame position, with strength 0–1."""
    image: Path
    frame_index: int
    strength: float = 1.0


class LoRACatalogEntry(TypedDict, total=False):
    id: str
    kind: str
    family: str
    supported: bool
    installed: bool
    requires: list[str]
    validation: str
    files: list[dict[str, Any]]
    repo_id: str
    revision: str
    collection: str
    gated: bool
    trigger: str | None
    min_seconds: int
    tested_workflows: list[str]


class VideoCapabilities(TypedDict):
    object: str
    schema_version: int
    model: str
    fps: int
    seconds: dict[str, int]
    sizes: list[str]
    default_size: str
    seed: dict[str, str]
    loras: dict[str, Any]
    motion_speed: dict[str, Any]
    lora_reference_strength: dict[str, Any]
    uploads: dict[str, Any]
    guiding_frames: dict[str, Any]


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


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not minimum <= value <= maximum or not math.isfinite(value)):
        raise EaselError(f"{name} must be a finite number between {minimum} and {maximum}")
    return float(value)


def _integer(value: Any, name: str, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise EaselError(f"{name} must be an integer between {minimum} and {maximum}")


def _lora_selections(value: str | list[LoRASelection] | None) -> list[LoRASelection]:
    if value is None:
        return []
    if isinstance(value, str):
        if len(value) > 8192:
            raise EaselError("loras JSON is too large (maximum 8192 characters)")
        try:
            requested = json.loads(value)
        except (ValueError, RecursionError) as exc:
            raise EaselError("loras must be a JSON array of id/strength objects") from exc
        if not isinstance(requested, list):
            raise EaselError("loras must be a JSON array of id/strength objects")
        selections = []
        for spec in requested:
            if not isinstance(spec, dict) or set(spec) - {"id", "strength"} or "id" not in spec:
                raise EaselError("each LoRA must have an id and optional strength")
            selections.append(LoRASelection(spec["id"], spec.get("strength", 1)))
    elif isinstance(value, list):
        selections = value
    else:
        raise EaselError("loras must be LoRASelection objects or legacy JSON")
    if len(selections) > 4:
        raise EaselError("at most four LoRAs may be selected")
    for selection in selections:
        if (not isinstance(selection, LoRASelection) or not isinstance(selection.id, str)
                or len(selection.id) > 128 or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", selection.id)):
            raise EaselError("LoRA id must be a registered identifier, never a path or URL; consult video loras")
        _number(selection.strength, "LoRA strength", 0, 2)
    if len({selection.id for selection in selections}) != len(selections):
        raise EaselError("duplicate LoRA ids are not allowed")
    return selections


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

    def get_model_guide(self, model: str, mode: str) -> dict[str, Any]:
        """Read reference content from the configured API; never execute guide text."""
        for name, value in (("model", model), ("mode", mode)):
            if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", value):
                raise EaselError(f"Invalid guide {name}")
        with self._http.stream("GET", f"{self.base_url}/v1/models/{model}/guide",
                               params={"mode": mode}, headers=self._headers,
                               follow_redirects=False, timeout=self.timeout) as response:
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 16 * 1024:
                    raise EaselError("Model guide exceeds 16 KiB")
            return self._decode_response(httpx.Response(response.status_code,
                headers=response.headers, content=bytes(content)))

    def list_models(self) -> list[str]:
        body = self._get_json("/v1/models")
        try:
            data = body["data"]
            if not isinstance(data, list) or any(not isinstance(item.get("id"), str) for item in data):
                raise ValueError
            return [item["id"] for item in data]
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise EaselError("Easel API returned an invalid models response") from exc

    def list_loras(self) -> list[LoRACatalogEntry]:
        data = self._get_json("/v1/videos/loras").get("data")
        if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
            raise EaselError("Easel API returned an invalid LoRA catalog")
        for item in data:
            if (not isinstance(item.get("id"), str)
                    or any(key in item and not isinstance(item[key], bool) for key in ("supported", "installed"))
                    or ("requires" in item and (not isinstance(item["requires"], list)
                        or any(not isinstance(field, str) for field in item["requires"])))
                    or ("validation" in item and not isinstance(item["validation"], str))):
                raise EaselError("Easel API returned an invalid LoRA catalog")
        return cast(list[LoRACatalogEntry], data)

    def video_capabilities(self, model: str | None = None) -> VideoCapabilities:
        if model is None:
            body = self._get_json("/v1/videos/capabilities")
        else:
            body = self._decode_response(self._http.get(
                f"{self.base_url}/v1/videos/capabilities", params={"model": model},
                headers=self._headers, follow_redirects=False, timeout=self.timeout))
        if (body.get("object") != "video.capabilities" or type(body.get("schema_version")) is not int
                or body["schema_version"] != 1):
            raise EaselError("Easel API returned an unsupported video capabilities contract")
        return cast(VideoCapabilities, body)

    @staticmethod
    def _upload(stack: ExitStack, path: Path, *, audio: bool = False) -> tuple[str, Any, str]:
        path = Path(path)
        mime = AUDIO_FILE_TYPES.get(path.suffix.lower()) if audio else mimetypes.guess_type(path.name)[0]
        if audio and mime is None:
            raise EaselError("source_audio files must be WAV, MP3, FLAC, Ogg or M4A")
        if not audio and mime not in {"image/png", "image/jpeg", "image/webp"}:
            raise EaselError("Reference files must be PNG, JPEG or WebP")
        try:
            # Nonblocking open prevents a FIFO substituted after preflight from hanging.
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise EaselError("source_audio files must be regular files" if audio else "Reference files must be regular files")
                handle = os.fdopen(descriptor, "rb")
            except BaseException:
                os.close(descriptor)
                raise
        except OSError as exc:
            raise EaselError(f"Cannot open {'source_audio' if audio else 'reference image'}: {path}: {exc.strerror}") from exc
        return path.name, stack.enter_context(handle), mime

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
                     loras: str | list[LoRASelection] | None = None, motion_speed: float | None = None,
                     seed: int | None = None, lora_reference: Path | None = None,
                     lora_reference_strength: float | None = None,
                     guiding_frames: list[GuideFrame] | None = None,
                     source_audio: Path | None = None) -> dict[str, Any]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise EaselError("prompt is required")
        if not isinstance(model, str) or not model.strip():
            raise EaselError("model is required")
        _integer(seconds, "seconds", 1, 12)
        if size not in VIDEO_SIZES:
            raise EaselError("Unsupported video size")
        if seed is not None:
            _integer(seed, "seed", 0, 2**64 - 2)
        selections = _lora_selections(loras)
        ids = [selection.id for selection in selections]
        if camera_lora is not None:
            if not isinstance(camera_lora, str) or camera_lora not in CAMERA_LORAS:
                raise EaselError("Unknown camera_lora")
            ids.append("camera-" + camera_lora)
        if camera_lora_strength is not None:
            _number(camera_lora_strength, "camera_lora_strength", 0, 2)
            if camera_lora is None:
                raise EaselError("camera_lora_strength requires camera_lora")
        if len(ids) > 4 or len(set(ids)) != len(ids):
            raise EaselError("Select at most four LoRAs without duplicate ids, including camera_lora")
        if "ingredients" in ids:
            if seconds < 5 or lora_reference is None or len(ids) != 1:
                raise EaselError("ingredients requires lora_reference, at least 5 seconds and no other LoRAs")
        elif lora_reference is not None or lora_reference_strength is not None:
            raise EaselError("lora_reference and lora_reference_strength require ingredients")
        if lora_reference_strength is not None:
            _number(lora_reference_strength, "lora_reference_strength", 0, 1)
        if "slow-motion" in ids and motion_speed is None:
            raise EaselError("slow-motion requires motion_speed")
        if motion_speed is not None:
            _number(motion_speed, "motion_speed", 0.025, 1)
            if "slow-motion" not in ids:
                raise EaselError("motion_speed requires slow-motion")
        if {"cinemagraph", "slow-motion"}.intersection(ids) and input_reference is None:
            raise EaselError("cinemagraph and slow-motion require input_reference")
        if "cinemagraph" in ids and any(id.startswith("camera-") and id != "camera-static" for id in ids):
            raise EaselError("cinemagraph conflicts with moving camera LoRAs")
        guides = []
        if guiding_frames is not None:
            if not isinstance(guiding_frames, list) or not 1 <= len(guiding_frames) <= 8:
                raise EaselError("guiding_frames requires one through eight GuideFrame objects")
            if input_reference is not None or lora_reference is not None or "ingredients" in ids:
                raise EaselError("guiding_frames cannot be combined with input_reference or ingredients/lora_reference")
            for index, guide in enumerate(guiding_frames):
                if not isinstance(guide, GuideFrame):
                    raise EaselError("guiding_frames must contain GuideFrame objects")
                if not isinstance(guide.image, (str, Path)) or not str(guide.image):
                    raise EaselError("Each guiding frame requires an image path")
                _integer(guide.frame_index, "frame_index", 0, seconds * 24)
                strength = _number(guide.strength, "guide strength", 0, 1)
                guides.append({"image_index": index, "frame_index": guide.frame_index, "strength": strength})
            if len({guide["frame_index"] for guide in guides}) != len(guides):
                raise EaselError("guiding_frames must have unique frame indices")
        if source_audio is not None:
            capability = self.video_capabilities(model)
            audio_capability = capability.get("source_audio")
            if (capability.get("model") != model or not isinstance(audio_capability, dict)
                    or audio_capability.get("available") is not True):
                raise EaselError("source_audio is unavailable for the selected model; check current video capabilities")
        fields = {"prompt": prompt, "model": model, "seconds": seconds, "size": size,
                  "camera_lora": camera_lora, "camera_lora_strength": camera_lora_strength,
                  "loras": json.dumps([{"id": item.id, "strength": item.strength} for item in selections],
                                      separators=(",", ":"), allow_nan=False) if loras is not None else None,
                  "motion_speed": motion_speed, "seed": seed,
                  "lora_reference_strength": lora_reference_strength,
                  "guiding_frames": json.dumps(guides, separators=(",", ":")) if guides else None}
        # Non-file multipart tuples force multipart even for text-to-video requests.
        files = [(name, (None, str(value))) for name, value in fields.items() if value is not None]
        with ExitStack() as stack:
            total = 0
            references = [("input_reference", input_reference), ("lora_reference", lora_reference)]
            references.extend(("guiding_images", guide.image) for guide in guiding_frames or [])
            for name, path in references:
                if path is not None:
                    filename, handle, mime = self._upload(stack, path)
                    remaining = MAX_UPLOAD_BYTES - total
                    if os.fstat(handle.fileno()).st_size > remaining:
                        raise EaselError("Combined reference uploads exceed 32 MiB")
                    content = handle.read(remaining + 1)
                    total += len(content)
                    if total > MAX_UPLOAD_BYTES:
                        raise EaselError("Combined reference uploads exceed 32 MiB")
                    files.append((name, (filename, content, mime)))
            if source_audio is not None:
                filename, handle, mime = self._upload(stack, source_audio, audio=True)
                if os.fstat(handle.fileno()).st_size > MAX_AUDIO_BYTES:
                    raise EaselError("source_audio exceeds 32 MiB")
                content = handle.read(MAX_AUDIO_BYTES + 1)
                if not content or len(content) > MAX_AUDIO_BYTES:
                    raise EaselError("source_audio must be nonempty and at most 32 MiB")
                files.append(("source_audio", (filename, content, mime)))
            body = self._submit("/v1/videos", files=files)
        self._validate_receipt(body, "video")
        return body

    @staticmethod
    def _job_path(kind: str, job_id: str) -> str:
        prefix = {"image": "image_job_", "video": "video_"}.get(kind)
        # Current video receipts include colon-separated timing metadata.
        pattern = r"[A-Za-z0-9_.:-]+" if kind == "video" else r"[A-Za-z0-9_.-]+"
        if prefix is None or not isinstance(job_id, str) or not job_id.startswith(prefix) or not re.fullmatch(pattern, job_id):
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

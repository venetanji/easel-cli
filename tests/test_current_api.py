"""Offline contract tests pinned to Easel main 10f118a (no live submissions)."""
import base64
import json

import httpx
import pytest

from easel_cli.client import EaselClient, EaselError


def make_client(handler, **kwargs):
    return EaselClient("https://easel.test", "private-test-key", http_client=httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True), **kwargs)


def test_default_image_model_is_current_qwen():
    def handler(request):
        assert json.loads(request.content)["model"] == "qwen-image-2.1"
        return httpx.Response(200, json={"data": [{"b64_json": "aW1hZ2U="}]})
    assert make_client(handler).generate("a fox") == [b"image"]


def test_async_image_submission_preserves_receipt_and_preference():
    def handler(request):
        assert request.headers["prefer"] == "respond-async"
        assert request.headers["authorization"] == "Bearer private-test-key"
        assert json.loads(request.content) == {"prompt": "fox", "model": "qwen-image-2.1", "n": 1,
            "response_format": "url", "server": "video", "seed": 42}
        return httpx.Response(202, json={"id": "image_job_test", "status": "queued"})
    assert make_client(handler).submit_image("fox", server="video", seed=42)["id"] == "image_job_test"


def test_video_fields_are_multipart_even_without_references():
    def handler(request):
        assert request.url.path == "/v1/videos"
        assert request.headers["content-type"].startswith("multipart/form-data;")
        body = request.content.decode()
        for name, value in {"model": "ltx-2.5", "prompt": "fox", "seconds": "2", "size": "512x320",
            "seed": "42", "camera_lora": "static", "camera_lora_strength": "0.8",
            "loras": '[{"id":"camera-dolly-in","strength":0.8}]'}.items():
            assert f'name="{name}"\r\n\r\n{value}' in body
        return httpx.Response(200, json={"id": "video_test", "status": "queued"})
    receipt = make_client(handler).create_video("fox", seconds=2, size="512x320", seed=42,
        camera_lora="static", camera_lora_strength=0.8,
        loras='[{"id":"camera-dolly-in","strength":0.8}]')
    assert receipt["id"] == "video_test"


def test_video_reference_uploads_have_correct_fields_and_mime(tmp_path):
    source = tmp_path / "source.webp"
    source.write_bytes(b"reference")
    sheet = tmp_path / "sheet.png"
    sheet.write_bytes(b"sheet")
    def handler(request):
        content = request.content.decode()
        assert 'name="input_reference"; filename="source.webp"\r\nContent-Type: image/webp' in content
        assert 'name="lora_reference"; filename="sheet.png"\r\nContent-Type: image/png' in content
        assert 'name="lora_reference_strength"\r\n\r\n0.5' in content
        return httpx.Response(200, json={"id": "video_test", "status": "queued"})
    make_client(handler).create_video("fox", input_reference=source, lora_reference=sheet,
        lora_reference_strength=0.5, loras='[{"id":"ingredients"}]', seconds=5)


@pytest.mark.parametrize("operation", ["edits", "variations"])
def test_image_reference_operations_support_async(operation, tmp_path):
    source = tmp_path / "reference.jpg"
    source.write_bytes(b"ref")
    def handler(request):
        assert request.url.path == f"/v1/images/{operation}"
        assert request.headers["prefer"] == "respond-async"
        assert 'name="image[]"; filename="reference.jpg"' in request.content.decode()
        return httpx.Response(202, json={"id": "image_job_test", "status": "queued"})
    result = make_client(handler).image_request(operation, prompt="fox", images=[source], async_mode=True)
    assert result["id"] == "image_job_test"


def test_lora_discovery_preserves_capability_and_installation_fields():
    catalog = [{"id": "ingredients", "supported": True, "installed": False, "requires": ["lora_reference"]}]
    client = make_client(lambda request: httpx.Response(200, json={"data": catalog}))
    assert client.list_loras() == catalog


@pytest.mark.parametrize("kind,path", [("image", "/v1/images/jobs/image_job_test"), ("video", "/v1/videos/video_test")])
def test_wait_polls_without_submitting_and_returns_completed(kind, path):
    requests = []
    def handler(request):
        requests.append((request.method, request.url.path))
        return httpx.Response(200, json={"id": path.rsplit("/", 1)[1],
            "status": "queued" if len(requests) == 1 else "completed"})
    client = make_client(handler)
    result = client.wait_for_job(kind, path.rsplit("/", 1)[1], poll_interval=0.001)
    assert result["status"] == "completed"
    assert requests == [("GET", path), ("GET", path)]


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_wait_terminal_failure_includes_resumable_id_and_server_error(status):
    client = make_client(lambda request: httpx.Response(200, json={"id": "video_test", "status": status,
        "error": {"code": "upstream_execution_error", "message": "backend interrupted"}}))
    with pytest.raises(EaselError, match="video_test.*backend interrupted"):
        client.wait_for_job("video", "video_test")


def test_wait_timeout_contains_id_and_never_resubmits():
    methods = []
    def handler(request):
        methods.append(request.method)
        return httpx.Response(200, json={"id": "video_test", "status": "queued"})
    with pytest.raises(EaselError, match="Timed out.*video_test"):
        make_client(handler).wait_for_job("video", "video_test", timeout=0.005, poll_interval=0.01)
    assert methods == ["GET"]


def test_wait_recovers_transient_poll_failure_without_post():
    methods = []
    def handler(request):
        methods.append(request.method)
        if len(methods) == 1:
            return httpx.Response(503, json={"error": {"message": "temporarily unavailable"}}, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"id": "image_job_test", "status": "completed"})
    assert make_client(handler).wait_for_job("image", "image_job_test", poll_interval=0.001)["status"] == "completed"
    assert methods == ["GET", "GET"]


@pytest.mark.parametrize("status,code", [(404, "image_job_not_found"), (410, "image_job_expired"), (401, "invalid_api_key")])
def test_wait_does_not_retry_permanent_errors(status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"code": code, "message": "cannot retrieve"}})
    with pytest.raises(EaselError) as error:
        make_client(handler).wait_for_job("image", "image_job_test")
    assert error.value.status_code == status
    assert error.value.code == code
    assert len(calls) == 1


def test_submit_transport_failure_is_not_retried():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("receipt lost", request=request)
    with pytest.raises(EaselError, match="ambiguous.*not.*resubmit"):
        make_client(handler).create_video("fox")
    assert len(calls) == 1


def test_api_errors_keep_code_param_retry_after_and_redact_key():
    client = make_client(lambda request: httpx.Response(429, json={"error": {
        "message": "private-test-key queue full", "code": "queue_full", "param": "server"}}, headers={"Retry-After": "5"}))
    with pytest.raises(EaselError) as error:
        client.submit_image("fox")
    assert error.value.code == "queue_full"
    assert error.value.param == "server"
    assert error.value.retry_after == 5
    assert "private-test-key" not in str(error.value)


def test_authenticated_url_images_support_all_sixteen_outputs():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"image")
    client = make_client(handler)
    result = client.images_from_response({"data": [{"url": f"https://easel.test/v1/images/jobs/image_job_test/content/{i}"} for i in range(16)]})
    assert result == [b"image"] * 16
    assert len(calls) == 16
    assert all(r.headers["authorization"] == "Bearer private-test-key" for r in calls)


@pytest.mark.parametrize("url", ["https://evil.test/file.png", "http://easel.test/v1/images/view?filename=x", "https://easel.test/admin", "//evil.test/image"])
def test_download_refuses_untrusted_image_urls_before_auth_transmission(url):
    calls = []
    client = make_client(lambda request: calls.append(request))
    with pytest.raises(EaselError, match="download URL"):
        client.images_from_response({"data": [{"url": url}]})
    assert not calls


def test_download_rejects_redirect_without_contacting_target():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.test/stolen"})
    with pytest.raises(EaselError):
        make_client(handler).download_video("video_test")
    assert calls == ["https://easel.test/v1/videos/video_test/content"]


def test_download_byte_limit_is_enforced():
    client = make_client(lambda request: httpx.Response(200, content=b"large-video"), max_download_bytes=4)
    with pytest.raises(EaselError, match="exceeds.*byte"):
        client.download_video("video_test")


def test_base64_image_size_limit_is_enforced():
    client = make_client(lambda request: httpx.Response(200), max_download_bytes=4)
    with pytest.raises(EaselError, match="exceeds.*byte"):
        client.images_from_response({"data": [{"b64_json": base64.b64encode(b"large-image").decode()}]})


@pytest.mark.parametrize("job_id", ["video_../admin", "../admin", "video_test?leak=1"])
def test_unsafe_job_ids_are_rejected_before_request(job_id):
    calls = []
    client = make_client(lambda request: calls.append(request))
    with pytest.raises(EaselError, match="job ID"):
        client.download_video(job_id)
    assert calls == []


@pytest.mark.parametrize("url", ["http://easel.test", "https://user:password@easel.test", "https://easel.test?token=value", "https://easel.test#secret"])
def test_unsafe_base_url_is_rejected(url):
    with pytest.raises(EaselError, match="base URL"):
        EaselClient(url, "test-key")


@pytest.mark.parametrize("body", [{"status": "queued"}, {"id": "video_test", "status": "invented"}])
def test_invalid_post_receipt_warns_of_ambiguous_submission(body):
    client = make_client(lambda request: httpx.Response(200, json=body))
    with pytest.raises(EaselError, match="ambiguous.*not.*resubmit"):
        client.create_video("fox")


def test_invalid_receipt_status_keeps_known_id_for_recovery():
    client = make_client(lambda request: httpx.Response(200, json={"id": "video_test", "status": "future_status"}))
    with pytest.raises(EaselError, match="video_test.*do not resubmit"):
        client.create_video("fox")


def test_successful_non_json_submission_is_ambiguous_not_safe_to_retry():
    client = make_client(lambda request: httpx.Response(200, text="accepted but broken receipt"))
    with pytest.raises(EaselError, match="ambiguous.*do not.*resubmit"):
        client.create_video("fox")


def test_error_metadata_redacts_echoed_key():
    client = make_client(lambda request: httpx.Response(400, json={"error": {
        "message": "bad", "code": "private-test-key", "param": "private-test-key"}}))
    with pytest.raises(EaselError) as error:
        client.submit_image("fox")
    assert error.value.code == "[REDACTED]"
    assert error.value.param == "[REDACTED]"


@pytest.mark.parametrize("status", [[], {}])
def test_malformed_status_is_reported_with_known_receipt_id(status):
    client = make_client(lambda request: httpx.Response(200, json={"id": "video_test", "status": status}))
    with pytest.raises(EaselError, match="video_test.*do not resubmit"):
        client.create_video("fox")


def test_download_error_body_respects_byte_cap():
    chunks = []
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                chunks.append(1)
                yield b"x" * 65536
    client = make_client(lambda request: httpx.Response(503, stream=Body()), max_download_bytes=65536)
    with pytest.raises(EaselError, match="exceeds.*byte"):
        client.download_video("video_test")
    assert len(chunks) <= 2


@pytest.mark.parametrize("base_url", ["https://easel.test/v1", "https://easel.test/proxy/v1/", "https://easel.test/proxy"])
def test_api_base_url_normalizes_exact_v1_suffix(base_url):
    paths = []
    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"data": []})
    client = EaselClient(base_url, "test", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.list_models() == []
    assert paths == (["/proxy/v1/models"] if "/proxy" in base_url else ["/v1/models"])


def test_terminal_job_error_code_is_redacted():
    client = make_client(lambda request: httpx.Response(200, json={"id": "video_test", "status": "failed",
        "error": {"message": "failed", "code": "private-test-key"}}))
    with pytest.raises(EaselError) as error:
        client.wait_for_job("video", "video_test")
    assert error.value.code == "[REDACTED]"

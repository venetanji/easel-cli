import base64

import httpx
import pytest

from easel_cli.client import EaselClient, EaselError


def test_generate_sends_openai_request_and_decodes_png():
    png = b"\x89PNG\r\n\x1a\nimage"
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["json"] = request.read().decode()
        return httpx.Response(200, json={"created": 1, "data": [{"b64_json": base64.b64encode(png).decode()}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = EaselClient("https://easel.ait4x.org", "test-key", http_client=http)
    images = client.generate("a red fox", model="flux2-4b", size="768x1024", n=1, seed=42)

    assert images == [png]
    assert seen["url"] == "https://easel.ait4x.org/v1/images/generations"
    assert seen["authorization"] == "Bearer test-key"
    assert __import__("json").loads(seen["json"]) == {
        "prompt": "a red fox", "model": "flux2-4b", "size": "768x1024", "n": 1, "seed": 42
    }
    http.close()


def test_generate_reports_openai_api_error():
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": {"message": "invalid or missing API key"}})
    ))
    client = EaselClient("https://easel.ait4x.org", "wrong", http_client=http)

    with pytest.raises(EaselError, match="invalid or missing API key") as caught:
        client.generate("a red fox")

    assert caught.value.status_code == 401
    http.close()


def test_generate_rejects_missing_image_data():
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{}]})
    ))
    client = EaselClient("https://easel.ait4x.org", "test-key", http_client=http)

    with pytest.raises(EaselError, match="response did not contain b64_json image data"):
        client.generate("a red fox")
    http.close()


def test_list_models_returns_model_ids():
    http = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"id": "flux2-9b"}, {"id": "qwen-image-2.1"}]})
    ))
    client = EaselClient("https://easel.ait4x.org", "test-key", http_client=http)

    assert client.list_models() == ["flux2-9b", "qwen-image-2.1"]
    http.close()

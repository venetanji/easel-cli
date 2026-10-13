"""Advanced video validation must fail before sending any upload or POST."""
import json
import os
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest

from easel_cli import client as module
from easel_cli.client import EaselClient, EaselError


def api(handler=None):
    calls = []
    def request(request):
        calls.append(request)
        if handler:
            return handler(request)
        return httpx.Response(200, json={"id": "video_test", "status": "queued"})
    return EaselClient("https://easel.test", "test-key", http_client=httpx.Client(
        transport=httpx.MockTransport(request))), calls


def parts(request):
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content)
    return [(part.get_param("name", header="content-disposition"), part.get_filename(),
             part.get_content_type(), part.get_payload(decode=True)) for part in message.iter_parts()]


def test_typed_loras_and_guides_serialize_without_local_paths(tmp_path):
    first, last = tmp_path / "first.png", tmp_path / "last.webp"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    client, calls = api()
    client.create_video("fox", seconds=2, seed=2**64-2,
        loras=[module.LoRASelection("camera-static", 0.8)],
        guiding_frames=[module.GuideFrame(first, 1, 0.5), module.GuideFrame(last, 48)])
    data = parts(calls[0])
    fields = {name: value for name, filename, _, value in data if filename is None}
    assert json.loads(fields["loras"]) == [{"id": "camera-static", "strength": 0.8}]
    assert json.loads(fields["guiding_frames"]) == [
        {"image_index": 0, "frame_index": 1, "strength": 0.5},
        {"image_index": 1, "frame_index": 48, "strength": 1.0}]
    assert fields["seed"] == b"18446744073709551614"
    assert [(name, filename, mime, body) for name, filename, mime, body in data if filename] == [
        ("guiding_images", "first.png", "image/png", b"first"),
        ("guiding_images", "last.webp", "image/webp", b"last")]
    assert str(tmp_path).encode() not in calls[0].content
    assert len(calls) == 1


@pytest.mark.parametrize("kwargs", [
    {"seconds": True}, {"seconds": 1.5}, {"seconds": 0}, {"seconds": 13},
    {"seed": True}, {"seed": 1.5}, {"seed": -1}, {"seed": 2**64-1},
    {"size": "123x456"}, {"prompt": "   "}, {"camera_lora": "../../custom"},
    {"camera_lora_strength": 1}, {"camera_lora": "static", "camera_lora_strength": float("nan")},
    {"camera_lora": "static", "camera_lora_strength": 2.1},
    {"loras": '[{"id":"camera-static","strength":true}]'},
    {"loras": '[{"id":"camera-static","strength":NaN}]'},
    {"loras": '[{"id":"camera-static","strength":-1}]'},
    {"loras": '[{"id":"camera-static","filename":"bad"}]'},
    {"loras": '[{"id":"../custom"}]'}, {"loras": '[{"id":"https://custom"}]'},
    {"loras": '{}'}, {"loras": '['}, {"loras": ' ' * 8193},
    {"loras": '[{"id":"camera-static"},{"id":"camera-static"}]'},
    {"camera_lora": "static", "loras": '[{"id":"camera-static"}]'},
    {"loras": '[{"id":"slow-motion"}]'},
    {"loras": '[{"id":"cinemagraph"}]'},
    {"loras": '[{"id":"ingredients"}]'},
    {"motion_speed": 0.2}, {"lora_reference_strength": 0.5},
])
def test_invalid_controls_fail_before_request(kwargs):
    client, calls = api()
    kwargs = {"prompt": "fox", **kwargs}
    with pytest.raises(EaselError):
        client.create_video(**kwargs)
    assert calls == []


@pytest.mark.parametrize("frames", [[], [(0, 1), (0, 0.5)], [(-1, 1)], [(49, 1)],
    [(True, 1)], [(0.5, 1)], [(0, True)], [(0, float("inf"))], [(0, -0.1)], [(0, 1.1)],
    [(i, 1) for i in range(9)]])
def test_invalid_guides_fail_before_request(tmp_path, frames):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    client, calls = api()
    with pytest.raises(EaselError):
        client.create_video("fox", seconds=2, guiding_frames=[
            module.GuideFrame(image, index, strength) for index, strength in frames])
    assert calls == []


@pytest.mark.parametrize("extra", ["input_reference", "lora_reference", "ingredients", "slow-motion", "cinemagraph"])
def test_guides_reject_conflicting_modes(tmp_path, extra):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    client, calls = api()
    kwargs = {extra: image} if extra.endswith("reference") else {"loras": json.dumps([{"id": extra}])}
    with pytest.raises(EaselError):
        client.create_video("fox", guiding_frames=[module.GuideFrame(image, 0)], **kwargs)
    assert calls == []


@pytest.mark.parametrize("kind", ["missing", "directory", "fifo", "unsupported", "oversize", "combined"])
def test_uploads_are_regular_images_under_combined_limit(tmp_path, kind):
    image = tmp_path / "image.png"
    if kind == "directory":
        image.mkdir()
    elif kind == "fifo":
        os.mkfifo(image)
    elif kind == "unsupported":
        image = tmp_path / "image.svg"
        image.write_bytes(b"image")
    elif kind in {"oversize", "combined"}:
        with image.open("wb") as handle:
            handle.truncate(33554433 if kind == "oversize" else 16777217)
    client, calls = api()
    with pytest.raises(EaselError):
        client.create_video("fox", guiding_frames=[module.GuideFrame(image, 0)] + (
            [module.GuideFrame(image, 1)] if kind == "combined" else []))
    assert calls == []


def test_capabilities_get_preserves_metadata():
    body = {"object": "video.capabilities", "schema_version": 1, "model": "ltx-2.5",
        "guiding_frames": {"supported": True, "available": False, "validation": "graph_contract_tested"},
        "future_metadata": {"key": "value"}}
    client, calls = api(lambda _: httpx.Response(200, json=body))
    assert client.video_capabilities() == body
    assert [(r.method, r.url.path) for r in calls] == [("GET", "/v1/videos/capabilities")]


@pytest.mark.parametrize("body", [{}, {"object": "video.capabilities", "schema_version": 2},
    {"object": "video.capabilities", "schema_version": True}, {"object": "list", "schema_version": 1}])
def test_capabilities_unknown_contract_fails_explicitly(body):
    client, _ = api(lambda _: httpx.Response(200, json=body))
    with pytest.raises(EaselError, match="capabilit"):
        client.video_capabilities()


@pytest.mark.parametrize("adapter", ["future-adapter", "ltx-2.3-future-adapter"])
def test_safe_future_adapter_id_is_preserved_for_server_registry_validation(adapter):
    client, calls = api()
    client.create_video("fox", loras=json.dumps([{"id": adapter, "strength": 0.5}]))
    assert json.loads(next(value for name, _, _, value in parts(calls[0]) if name == "loras")) == [
        {"id": adapter, "strength": 0.5}]


@pytest.mark.parametrize("kwargs", [
    {"loras": [True]}, {"loras": {"id": "camera-static"}},
    {"loras": '[{"id":"' + 'a'*129 + '"}]'},
    {"loras": json.dumps([{"id": f"adapter-{i}"} for i in range(5)])},
    {"camera_lora": "static", "loras": json.dumps([{"id": f"adapter-{i}"} for i in range(4)])},
    {"loras": '[{"id":"ingredients"}]', "lora_reference_strength": float("nan")},
])
def test_invalid_selection_shapes_and_limits_do_not_submit(kwargs):
    client, calls = api()
    with pytest.raises(EaselError):
        client.create_video("fox", **kwargs)
    assert calls == []


@pytest.mark.parametrize("kwargs", [
    {"loras": '[{"id":"slow-motion"}]', "motion_speed": 0.024},
    {"loras": '[{"id":"slow-motion"}]', "motion_speed": True},
    {"loras": '[{"id":"slow-motion"}]', "motion_speed": float("inf")},
    {"loras": '[{"id":"cinemagraph"}]', "camera_lora": "dolly-in"},
    {"loras": '[{"id":"ingredients"}]', "seconds": 4},
    {"loras": '[{"id":"ingredients"}]', "lora_reference_strength": 1.1},
    {"loras": '[{"id":"ingredients"}]', "camera_lora": "static"},
])
def test_reference_adapter_constraints_before_request(tmp_path, kwargs):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    reference = "lora_reference" if "ingredients" in kwargs["loras"] else "input_reference"
    client, calls = api()
    with pytest.raises(EaselError):
        client.create_video("fox", **{"seconds": 5, reference: image, **kwargs})
    assert calls == []


@pytest.mark.parametrize("index", [0, 1, 24, 48])
def test_every_pixel_frame_position_is_supported(tmp_path, index):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    client, calls = api()
    client.create_video("fox", seconds=2, guiding_frames=[module.GuideFrame(image, index)])
    assert len(calls) == 1


def test_guide_requires_real_image_path():
    client, calls = api()
    with pytest.raises(EaselError, match="image"):
        client.create_video("fox", guiding_frames=[module.GuideFrame(None, 0)])
    assert calls == []


@pytest.mark.parametrize("fail", [False, True])
def test_opened_references_are_closed_on_success_and_later_validation_failure(tmp_path, monkeypatch, fail):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    original = EaselClient._upload
    opened = []
    def upload(stack, path):
        result = original(stack, path)
        opened.append(result[1])
        return result
    monkeypatch.setattr(EaselClient, "_upload", staticmethod(upload))
    client, calls = api()
    frames = [module.GuideFrame(image, 0)]
    if fail:
        frames.append(module.GuideFrame(tmp_path / "missing.png", 1))
        with pytest.raises(EaselError):
            client.create_video("fox", guiding_frames=frames)
        assert not calls
    else:
        client.create_video("fox", guiding_frames=frames)
    assert len(opened) == 1 and opened[0].closed


def test_exact_combined_upload_limit_is_accepted(tmp_path):
    image = tmp_path / "image.png"
    with image.open("wb") as handle:
        handle.truncate(16 * 1024 * 1024)
    client, calls = api()
    client.create_video("fox", guiding_frames=[module.GuideFrame(image, 0), module.GuideFrame(image, 1)])
    assert len(calls) == 1


def test_slow_motion_fields_survive_typed_selection(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    client, calls = api()
    client.create_video("fox", input_reference=image, loras=[module.LoRASelection("slow-motion")], motion_speed=0.2)
    fields = {name: body for name, filename, _, body in parts(calls[0]) if filename is None}
    assert fields["motion_speed"] == b"0.2"
    assert json.loads(fields["loras"]) == [{"id": "slow-motion", "strength": 1}]


@pytest.mark.parametrize("entry", [{}, {"id": 3}, {"id": "ingredients", "supported": "yes"},
    {"id": "ingredients", "installed": 1}, {"id": "ingredients", "requires": "lora_reference"},
    {"id": "ingredients", "validation": False}])
def test_invalid_typed_discovery_entries_fail_explicitly(entry):
    client, _ = api(lambda _: httpx.Response(200, json={"data": [entry]}))
    with pytest.raises(EaselError, match="LoRA catalog"):
        client.list_loras()

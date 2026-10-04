"""Opt-in, in-process test against an existing Easel server checkout.

Run with that checkout's dev Python, EASEL_SERVER_SOURCE and this repo's src on
PYTHONPATH. All backends are fake; no network or model execution occurs.
"""
import dataclasses
import os
import sys
import time
from pathlib import Path

import pytest

SERVER_SOURCE = os.environ.get("EASEL_SERVER_SOURCE")
if not SERVER_SOURCE:
    pytest.skip("Set EASEL_SERVER_SOURCE to an Easel checkout", allow_module_level=True)
sys.path.insert(0, str(Path(SERVER_SOURCE).resolve()))

from easel_cli.client import EaselClient, EaselError, GuideFrame, LoRASelection


class Backend:
    def __init__(self, video=False):
        self.video = video
        self.submissions = []
        self.finished = False
        self.uploads = []
        self.installed_loras = []
        self.guides_available = False

    async def submit(self, graph):
        self.submissions.append(graph)
        return f"contract-prompt-{len(self.submissions)}"

    async def queue(self):
        pending = [[1, "contract-prompt-1"]] if self.submissions and not self.finished else []
        return {"queue_pending": pending, "queue_running": []}

    async def history_item(self, prompt_id):
        if not self.finished:
            return None
        return {"status": {"status_str": "success", "completed": True,
            "messages": [["execution_success", {"timestamp": int(time.time()) * 1000}]]},
            "outputs": {"save": {"videos" if self.video else "images": self.refs()}}}

    def refs(self):
        return [{"filename": "test.mp4" if self.video else "test.png", "type": "output"}]

    async def wait(self, *args, **kwargs):
        return self.refs()

    async def fetch(self, ref):
        return b"FAKE-MP4" if self.video else b"FAKE-PNG"

    async def upload_image(self, data, filename, content_type=None):
        self.uploads.append((data, filename, content_type))
        return "reference.png", ""

    async def object_info(self, name):
        if name == "LoraLoaderModelOnly":
            return {name: {"input": {"required": {"lora_name": [self.installed_loras]}}}}
        keys = {"LTXVAddGuide": ["positive", "negative", "vae", "latent", "image", "frame_idx", "strength"],
                "LTXVCropGuides": ["positive", "negative", "latent"]}.get(name)
        if keys and self.guides_available:
            schema = {"positive": ["CONDITIONING"], "negative": ["CONDITIONING"],
                      "vae": ["VAE"], "latent": ["LATENT"], "image": ["IMAGE"],
                      "frame_idx": ["INT", {"default": 0, "min": -9999, "max": 9999, "step": 1}],
                      "strength": ["FLOAT", {"default": 1.0, "min": 0.0, "max": 10.0, "step": 0.01}]}
            return {name: {"input": {"required": {key: schema[key] for key in keys}},
                           "output": ["CONDITIONING", "CONDITIONING", "LATENT"]}}
        return {}


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("EASEL_JOB_DIR", str(tmp_path / "import-cache"))
    from fastapi.testclient import TestClient
    from easel.app import create_app
    from easel.config import Settings
    image, video = Backend(), Backend(video=True)
    settings = dataclasses.replace(Settings.from_env({}), api_key="offline-contract-key",
        comfy_video_url="http://fake-video", image_job_dir=str(tmp_path / "jobs"))
    app = create_app(settings=settings, comfy=image, comfy_video=video)
    with TestClient(app, base_url="https://easel.test") as http:
        client = EaselClient("https://easel.test/v1", "offline-contract-key", http_client=http)
        yield client, image, video


def test_current_model_and_adapter_discovery(api):
    client, _, _ = api
    assert client.list_models() == ["qwen-image-2.1", "ltx-2.5"]
    catalog = client.list_loras()
    assert any(item["id"] == "ingredients" for item in catalog)
    assert all("supported" in item and item["installed"] is False for item in catalog)


def test_legacy_sync_generation_uses_current_model(api):
    client, image, _ = api
    assert client.generate("red fox", size="512x512") == [b"FAKE-PNG"]
    assert len(image.submissions) == 1


@pytest.mark.parametrize("operation", ["generations", "edits", "variations"])
@pytest.mark.parametrize("response_format", ["url", "b64_json"])
def test_durable_image_receipt_poll_and_content(api, tmp_path, operation, response_format):
    client, image, _ = api
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"FAKE-REFERENCE")
    receipt = client.image_request(operation, prompt="red fox", images=[reference],
        async_mode=True, response_format=response_format)
    assert receipt["id"].startswith("image_job_")
    assert receipt["status"] == "queued"
    assert client.get_job("image", receipt["id"])["status"] == "queued"
    image.finished = True
    result = client.wait_for_job("image", receipt["id"], timeout=2, poll_interval=0.01)
    assert client.images_from_response(result) == [b"FAKE-PNG"]
    assert len(image.submissions) == 1
    assert len(image.uploads) == (0 if operation == "generations" else 1)


@pytest.mark.parametrize("reference", [False, True])
def test_video_multipart_receipt_queue_wait_and_content(api, tmp_path, reference):
    client, _, video = api
    image = tmp_path / "reference.webp"
    image.write_bytes(b"FAKE-REFERENCE")
    receipt = client.create_video("red fox", seconds=2, size="512x320", seed=42,
        input_reference=image if reference else None)
    job_id = receipt["id"]
    assert client.get_job("video", job_id)["status"] == "queued"
    assert client.video_queue(job_id)["queue_position"] == 1
    with pytest.raises(EaselError) as error:
        client.download_video(job_id)
    assert error.value.status_code == 409
    video.finished = True
    assert client.wait_for_job("video", job_id, timeout=2)["status"] == "completed"
    assert client.download_video(job_id) == b"FAKE-MP4"
    assert len(video.submissions) == 1
    assert len(video.uploads) == int(reference)


def test_retired_model_fails_before_backend_submission(api):
    client, image, _ = api
    with pytest.raises(EaselError) as error:
        client.submit_image("fox", model="flux2-9b")
    assert error.value.status_code == 400
    assert error.value.code == "model_not_found"
    assert image.submissions == []


def test_capabilities_distinguishes_support_from_available_nodes(api):
    client, _, video = api
    capabilities = client.video_capabilities()
    assert capabilities["schema_version"] == 1
    assert capabilities["guiding_frames"]["supported"] is True
    assert capabilities["guiding_frames"]["available"] is False
    assert capabilities["guiding_frames"]["validation"] == "graph_contract_tested"
    assert capabilities["seed"]["max"] == "18446744073709551614"
    assert capabilities["uploads"]["max_total_bytes"] == 33554432
    video.guides_available = True
    assert client.video_capabilities()["guiding_frames"]["available"] is True
    assert video.submissions == [] and video.uploads == []


def test_guiding_frames_typed_lora_and_exact_seed_reach_both_graph_passes(api, tmp_path):
    from PIL import Image
    from easel.video_loras import VIDEO_LORAS
    client, _, video = api
    video.guides_available = True
    video.installed_loras = [VIDEO_LORAS["camera-static"]["files"][0]["filename"]]
    first, last = tmp_path / "first.png", tmp_path / "last.png"
    Image.new("RGB", (8, 8), "red").save(first)
    Image.new("RGB", (8, 8), "blue").save(last)
    receipt = client.create_video("one coherent shot", seconds=2, size="512x320", seed=2**64-2,
        loras=[LoRASelection("camera-static", 0.8)],
        guiding_frames=[GuideFrame(first, 1, 0.7), GuideFrame(last, 48, 0.3)])
    assert receipt["id"].startswith("video_")
    assert len(video.submissions) == 1 and len(video.uploads) == 2
    graph = video.submissions[0]
    guides = [node["inputs"] for node in graph.values() if node["class_type"] == "LTXVAddGuide"]
    assert [node["frame_idx"] for node in guides] == [1, 48, 1, 48]
    assert [node["strength"] for node in guides] == [0.7, 0.3, 0.7, 0.3]
    assert [node["inputs"]["noise_seed"] for node in graph.values()
            if node["class_type"] == "RandomNoise"] == [2**64-2, 2**64-1]
    video.finished = True
    assert client.wait_for_job("video", receipt["id"], timeout=2)["status"] == "completed"
    assert client.download_video(receipt["id"]) == b"FAKE-MP4"
    assert len(video.submissions) == 1


def test_guide_missing_nodes_rejects_before_upload(api, tmp_path):
    from PIL import Image
    client, _, video = api
    image = tmp_path / "guide.png"
    Image.new("RGB", (8, 8), "red").save(image)
    with pytest.raises(EaselError) as error:
        client.create_video("fox", guiding_frames=[GuideFrame(image, 0)])
    assert error.value.status_code == 503
    assert video.uploads == [] and video.submissions == []


@pytest.mark.parametrize("id", ["clean-plate", "unknown-future-adapter"])
def test_registry_rejects_unsupported_and_unknown_ids_before_upload(api, tmp_path, id):
    client, _, video = api
    image = tmp_path / "image.png"
    image.write_bytes(b"image")
    with pytest.raises(EaselError) as error:
        client.create_video("fox", loras=[LoRASelection(id)], input_reference=image)
    assert error.value.status_code == 400
    assert video.uploads == [] and video.submissions == []

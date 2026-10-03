import json

import httpx
import pytest

from easel_cli import cli
from easel_cli.client import EaselClient


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EASEL_API_KEY", "private-test-key")
    calls = []
    responses = []
    def handler(request):
        calls.append(request)
        return responses.pop(0)
    monkeypatch.setattr(cli, "EaselClient", lambda base_url, api_key, **kwargs: EaselClient(
        base_url, api_key, http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs))
    return calls, responses


def test_generate_cli_defaults_to_qwen(api, tmp_path):
    calls, responses = api
    responses.append(httpx.Response(200, json={"data": [{"b64_json": "aW1hZ2U="}]}))
    assert cli.main(["generate", "fox"]) == 0
    assert json.loads(calls[0].content)["model"] == "qwen-image-2.1"
    assert (tmp_path / "easel.png").read_bytes() == b"image"


def test_async_image_prints_and_saves_receipt_without_polling(api, tmp_path, capsys):
    calls, responses = api
    receipt = {"id": "image_job_test", "status": "queued"}
    responses.append(httpx.Response(202, json=receipt))
    assert cli.main(["generate", "fox", "--async", "--receipt", "receipt.json"]) == 0
    assert json.loads(capsys.readouterr().out) == receipt
    assert json.loads((tmp_path / "receipt.json").read_text()) == receipt
    assert [r.method for r in calls] == ["POST"]
    assert not (tmp_path / "easel.png").exists()


def test_video_create_wait_saves_receipt_before_poll_and_mp4(api, tmp_path, capsys):
    calls, responses = api
    responses.extend([httpx.Response(200, json={"id": "video_test", "status": "queued"}),
        httpx.Response(200, json={"id": "video_test", "status": "completed"}),
        httpx.Response(200, content=b"mp4")])
    assert cli.main(["video", "create", "fox", "--seconds", "2", "--size", "512x320",
        "--wait", "--receipt", "receipt.json", "--output", "clip.mp4"]) == 0
    assert json.loads((tmp_path / "receipt.json").read_text())["id"] == "video_test"
    assert (tmp_path / "clip.mp4").read_bytes() == b"mp4"
    assert [(r.method, r.url.path) for r in calls] == [("POST", "/v1/videos"),
        ("GET", "/v1/videos/video_test"), ("GET", "/v1/videos/video_test/content")]
    assert '"id": "video_test"' in capsys.readouterr().out


def test_existing_output_blocks_generation_without_network(api, tmp_path, capsys):
    calls, _ = api
    (tmp_path / "easel.png").write_bytes(b"original")
    assert cli.main(["generate", "fox"]) == 1
    assert not calls
    assert (tmp_path / "easel.png").read_bytes() == b"original"
    assert "exists" in capsys.readouterr().err


def test_existing_receipt_blocks_submission_without_network(api, tmp_path):
    calls, _ = api
    (tmp_path / "receipt.json").write_text("original")
    assert cli.main(["video", "create", "fox", "--receipt", "receipt.json"]) == 1
    assert not calls
    assert (tmp_path / "receipt.json").read_text() == "original"


def test_image_job_wait_downloads_all_fanout_outputs(api, tmp_path):
    calls, responses = api
    responses.append(httpx.Response(200, json={"id": "image_job_test", "status": "completed",
        "data": [{"b64_json": "aW1hZ2U="}] * 16}))
    assert cli.main(["image-job", "wait", "image_job_test", "-o", "batch.png"]) == 0
    assert len(list(tmp_path.glob("batch-*.png"))) == 16
    assert [r.method for r in calls] == ["GET"]


def test_video_status_and_queue_are_json_and_read_only(api, capsys):
    calls, responses = api
    responses.extend([httpx.Response(200, json={"id": "video_test", "status": "queued"}),
        httpx.Response(200, json={"id": "video_test", "queue_position": 1, "estimated_wait_seconds": None})])
    assert cli.main(["video", "status", "video_test"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "queued"
    assert cli.main(["video", "queue", "video_test"]) == 0
    assert json.loads(capsys.readouterr().out)["estimated_wait_seconds"] is None
    assert [r.url.path for r in calls] == ["/v1/videos/video_test", "/v1/videos/queue/video_test"]


def test_failure_reports_receipt_id_without_saving_output(api, tmp_path, capsys):
    calls, responses = api
    responses.extend([httpx.Response(200, json={"id": "video_test", "status": "queued"}),
        httpx.Response(200, json={"id": "video_test", "status": "failed", "error": {"message": "render failed"}})])
    assert cli.main(["video", "create", "fox", "--wait"]) == 1
    streams = capsys.readouterr()
    assert '"id": "video_test"' in streams.out
    assert "video_test" in streams.err
    assert not (tmp_path / "easel.mp4").exists()


def test_download_refuses_overwrite_without_network(api, tmp_path):
    calls, _ = api
    (tmp_path / "clip.mp4").write_bytes(b"original")
    assert cli.main(["video", "download", "video_test", "-o", "clip.mp4"]) == 1
    assert not calls


def test_video_loras_discovery_keeps_capabilities(api, capsys):
    _, responses = api
    responses.append(httpx.Response(200, json={"data": [{"id": "ingredients", "supported": True, "installed": False}]}))
    assert cli.main(["video", "loras"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["installed"] is False


@pytest.mark.parametrize("flags", [["--seconds", "13"], ["--seconds", "1.5"], ["--wait-timeout", "nan"], ["--poll-interval", "0"]])
def test_invalid_video_flags_rejected_without_submission(api, flags):
    calls, _ = api
    with pytest.raises(SystemExit) as exc:
        cli.main(["video", "create", "fox", *flags])
    assert exc.value.code == 2
    assert not calls


def test_receipt_stdout_and_file_redact_server_secret_echo(api, tmp_path, capsys):
    _, responses = api
    responses.append(httpx.Response(200, json={"id": "video_test", "status": "queued",
        "metadata": {"message": "private-test-key"}}))
    assert cli.main(["video", "create", "fox", "--receipt", "receipt.json"]) == 0
    assert "private-test-key" not in capsys.readouterr().out
    assert "private-test-key" not in (tmp_path / "receipt.json").read_text()


def test_receipt_and_output_cannot_be_same_path(api):
    calls, _ = api
    assert cli.main(["video", "create", "fox", "--wait", "--receipt", "same.mp4", "-o", "same.mp4"]) == 1
    assert not calls


def test_json_secret_redaction_happens_before_escaping(capsys):
    key = 'fake"key\\with-é'
    cli._print_json({key: {"nested": [key, "prefix " + key]}}, key)
    output = json.loads(capsys.readouterr().out)
    assert output == {"[REDACTED]": {"nested": ["[REDACTED]", "prefix [REDACTED]"]}}


@pytest.mark.parametrize("flags", [["-n", "2", "-o", "batch.png", "--receipt", "batch-1.png"],
    ["-o", "batch", "--receipt", "batch.png"]])
def test_image_receipt_cannot_collide_with_numbered_or_normalized_output(api, flags):
    calls, _ = api
    assert cli.main(["generate", "fox", "--wait", *flags]) == 1
    assert not calls

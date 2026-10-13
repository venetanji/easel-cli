"""Interrupt advice must reflect the receipt actually exposed to the user."""
import json

import httpx
import pytest

from easel_cli import cli
from easel_cli.client import EaselClient


@pytest.fixture
def interrupted_api(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EASEL_API_KEY", "offline-key")
    calls = []
    replies = []

    def handler(request):
        calls.append(request)
        if replies:
            return replies.pop(0)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "EaselClient", lambda base_url, api_key, **kwargs: EaselClient(
        base_url, api_key, http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kwargs))
    return calls, replies


def test_interrupt_before_submission_does_not_claim_receipt(interrupted_api, monkeypatch, capsys):
    calls, _ = interrupted_api
    def interrupt(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "_check_new", interrupt)
    assert cli.main(["video", "create", "shot", "--receipt", "job.json"]) == 130
    assert not calls
    assert capsys.readouterr().err == "Interrupted.\n"


@pytest.mark.parametrize("argv", [["models"], ["video", "loras"], ["video", "status", "video_test"]])
def test_interrupt_read_only_does_not_claim_submission(interrupted_api, capsys, argv):
    calls, _ = interrupted_api
    assert cli.main(argv) == 130
    assert [request.method for request in calls] == ["GET"]
    assert capsys.readouterr().err == "Interrupted.\n"


@pytest.mark.parametrize("argv", [["generate", "shot"], ["generate", "shot", "--async"],
                                  ["video", "create", "shot"]])
def test_interrupt_inflight_without_receipt_warns_of_ambiguity(interrupted_api, capsys, argv):
    calls, _ = interrupted_api
    assert cli.main(argv) == 130
    streams = capsys.readouterr()
    assert streams.out == ""
    assert "No job receipt was printed" in streams.err
    assert "may have been accepted" in streams.err
    assert "do not resubmit automatically" in streams.err
    assert [request.method for request in calls] == ["POST"]


@pytest.mark.parametrize("argv,job_id", [(["generate", "shot", "--wait"], "image_job_test"),
                                        (["video", "create", "shot", "--wait"], "video_test")])
def test_interrupt_after_receipt_preserves_resume_advice(interrupted_api, capsys, argv, job_id):
    calls, replies = interrupted_api
    replies.append(httpx.Response(202, json={"id": job_id, "status": "queued"}))
    assert cli.main(argv) == 130
    streams = capsys.readouterr()
    assert json.loads(streams.out)["id"] == job_id
    assert "resume with the printed ID" in streams.err
    assert "do not resubmit" in streams.err
    assert [request.method for request in calls] == ["POST", "GET"]


def test_interrupt_writing_receipt_still_has_printed_id(interrupted_api, monkeypatch, capsys):
    calls, replies = interrupted_api
    replies.append(httpx.Response(202, json={"id": "video_test", "status": "queued"}))
    def interrupt(*args):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "_write_new", interrupt)
    assert cli.main(["video", "create", "shot", "--receipt", "job.json"]) == 130
    streams = capsys.readouterr()
    assert json.loads(streams.out)["id"] == "video_test"
    assert "resume with the printed ID" in streams.err
    assert [request.method for request in calls] == ["POST"]

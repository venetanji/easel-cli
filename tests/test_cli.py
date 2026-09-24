from pathlib import Path

from easel_cli import cli


def test_multiple_output_paths_add_number_before_suffix():
    assert cli.output_paths(Path("ideas.png"), 3) == [
        Path("ideas-1.png"), Path("ideas-2.png"), Path("ideas-3.png")
    ]


def test_single_image_keeps_requested_output_path():
    assert cli.output_paths(Path("images/final.webp"), 1) == [Path("images/final.webp")]


def test_generate_command_uses_dotenv_and_saves_all_images(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text("# local key\nEASEL_KEY=from-dotenv\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EASEL_API_KEY", raising=False)
    monkeypatch.delenv("EASEL_KEY", raising=False)
    calls = {}

    class FakeClient:
        def __init__(self, base_url, api_key, timeout):
            calls["config"] = (base_url, api_key, timeout)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def generate(self, prompt, **kwargs):
            calls["generate"] = (prompt, kwargs)
            return [b"png-one", b"png-two"]

    monkeypatch.setattr(cli, "EaselClient", FakeClient)
    output = tmp_path / "art.png"
    result = cli.main([
        "generate", "two concept sketches", "--output", str(output), "--n", "2",
        "--size", "768x1024", "--model", "flux2-4b", "--base-url", "https://example.test",
    ])

    assert result == 0
    assert calls["config"] == ("https://example.test", "from-dotenv", 600)
    assert calls["generate"] == (
        "two concept sketches",
        {"model": "flux2-4b", "size": "768x1024", "n": 2, "steps": None, "seed": None},
    )
    assert (tmp_path / "art-1.png").read_bytes() == b"png-one"
    assert (tmp_path / "art-2.png").read_bytes() == b"png-two"
    assert "Saved" in capsys.readouterr().out


def test_generate_without_key_exits_with_actionable_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("EASEL_API_KEY", raising=False)
    monkeypatch.delenv("EASEL_KEY", raising=False)

    result = cli.main(["generate", "a blue house"])

    assert result == 2
    assert "EASEL_API_KEY" in capsys.readouterr().err

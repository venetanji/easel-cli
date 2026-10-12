"""Command-line interface for Easel image and video jobs."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import httpx

from easel_cli.client import (EaselClient, EaselError, GuideFrame, LoRASelection,
                              IMAGE_MODEL, VIDEO_MODEL, VIDEO_SIZES)

DEFAULT_BASE_URL = "https://easel.ait4x.org"


def load_dotenv(path: Path = Path(".env"), environ: dict[str, str] | None = None) -> None:
    """Load simple KEY=VALUE entries without overriding existing environment values."""
    target = os.environ if environ is None else environ
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("export "):
            entry = entry[7:].lstrip()
        if "=" not in entry:
            continue
        name, value = entry.split("=", 1)
        name = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '\"'):
            value = value[1:-1]
        if name and name not in target:
            target[name] = value


def output_paths(output: Path, count: int) -> list[Path]:
    """Return deterministic output names, numbering batches before the suffix."""
    if not output.suffix:
        output = output.with_suffix(".png")
    if count == 1:
        return [output]
    return [output.with_name(f"{output.stem}-{index}{output.suffix}") for index in range(1, count + 1)]


def positive_number(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def lora_selection(value: str) -> LoRASelection:
    identity, separator, strength = value.partition("=")
    try:
        return LoRASelection(identity, float(strength) if separator else 1.0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("LoRA selection must be ID or ID=STRENGTH") from exc


def guide_frames(values: list[list[str]] | None) -> list[GuideFrame] | None:
    if values is None:
        return None
    try:
        return [GuideFrame(Path(image), int(frame), float(strength)) for frame, image, strength in values]
    except ValueError as exc:
        raise EaselError("--guide-frame requires integer FRAME, IMAGE path and numeric STRENGTH") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="easel", description="Generate images and videos with Easel")
    parser.add_argument("--version", action="version", version="easel-cli 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("generate", "generate images"), ("edit", "edit reference images"),
                            ("variation", "create a reference variation")):
        image = commands.add_parser(name, help=help_text)
        if name == "variation":
            image.add_argument("image", type=Path)
        else:
            image.add_argument("prompt")
        if name == "edit":
            image.add_argument("--image", action="append", type=Path, required=True,
                               help="reference image; repeat for multiple references")
        image.add_argument("--model", default=IMAGE_MODEL, help=f"model ID (default: {IMAGE_MODEL})")
        image.add_argument("--size", help="image dimensions, e.g. 1024x1024")
        image.add_argument("-n", "--n", type=int, choices=range(1, 5), default=1)
        image.add_argument("--steps", type=int)
        image.add_argument("--seed", type=int)
        image.add_argument("--server", choices=("image", "video"))
        image.add_argument("--response-format", choices=("b64_json", "url"), default=None)
        image.add_argument("--async", dest="async_mode", action="store_true", help="submit once and print the job receipt")
        _add_wait_options(image)
        _add_receipt_option(image)
        _add_output_option(image, "easel.png")
        _add_connection_options(image)

    models = commands.add_parser("models", help="discover current image and video model IDs")
    _add_connection_options(models)

    guide = commands.add_parser("model-guide", help="read shared model/mode prompt guidance as reference JSON")
    guide.add_argument("model")
    guide.add_argument("--mode", required=True)
    _add_connection_options(guide)

    jobs = commands.add_parser("image-job", help="retrieve accepted image jobs without resubmitting")
    actions = jobs.add_subparsers(dest="action", required=True)
    _add_job_commands(actions, "easel.png")

    video = commands.add_parser("video", help="create, inspect and download video jobs")
    actions = video.add_subparsers(dest="action", required=True)
    create = actions.add_parser("create", help="submit a video once and print its receipt")
    create.add_argument("prompt")
    create.add_argument("--model", default=VIDEO_MODEL)
    create.add_argument("--seconds", type=int, choices=range(1, 13), default=4)
    create.add_argument("--size", default="1280x720", choices=VIDEO_SIZES)
    create.add_argument("--input-reference", type=Path)
    create.add_argument("--source-audio", type=Path, help="WAV/MP3/FLAC/Ogg/M4A; conditions video and supplies its trimmed soundtrack")
    create.add_argument("--seed", type=int)
    create.add_argument("--camera-lora", choices=("dolly-in", "dolly-out", "dolly-left", "dolly-right", "jib-up", "jib-down", "static"))
    create.add_argument("--camera-lora-strength", type=float)
    selections = create.add_mutually_exclusive_group()
    selections.add_argument("--lora", action="append", type=lora_selection,
                            help="registered ID or ID=STRENGTH; repeat up to four times")
    selections.add_argument("--loras", help="legacy JSON array of registered id/strength selections; consult video loras")
    create.add_argument("--guide-frame", action="append", nargs=3, metavar=("FRAME", "IMAGE", "STRENGTH"),
                        help="guide at a pixel-frame index (0 through seconds*24); repeat up to eight times")
    create.add_argument("--motion-speed", type=float)
    create.add_argument("--lora-reference", type=Path)
    create.add_argument("--lora-reference-strength", type=float)
    _add_wait_options(create)
    _add_receipt_option(create)
    _add_output_option(create, "easel.mp4")
    _add_connection_options(create)
    _add_job_commands(actions, "easel.mp4")
    queue = actions.add_parser("queue", help="show queue counts and approximate completion ETA")
    queue.add_argument("job_id")
    _add_connection_options(queue)
    loras = actions.add_parser("loras", help="discover supported/installed adapter capabilities as JSON")
    _add_connection_options(loras)
    capabilities = actions.add_parser("capabilities", help="discover video limits and guide support/availability as JSON")
    _add_connection_options(capabilities)
    return parser


def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", help="API base URL (HTTPS, or HTTP loopback for local tests)")
    parser.add_argument("--api-key", help="bearer key; environment/.env is safer than shell history")
    parser.add_argument("--timeout", type=positive_number, default=600, help="per-request timeout in seconds (default: 600)")
    parser.add_argument("--max-download-mib", type=positive_number, default=512,
                        help="download/output byte cap in MiB (default: 512)")


def _add_wait_options(parser: argparse.ArgumentParser, *, flag: bool = True) -> None:
    if flag:
        parser.add_argument("--wait", action="store_true", help="poll accepted job and download completion")
    parser.add_argument("--wait-timeout", type=positive_number, default=600, help="polling time budget in seconds (default: 600)")
    parser.add_argument("--poll-interval", type=positive_number, default=2, help="seconds between status GETs (default: 2)")


def _add_receipt_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--receipt", type=Path, help="also save accepted receipt JSON to a new file")


def _add_output_option(parser: argparse.ArgumentParser, default: str) -> None:
    parser.add_argument("-o", "--output", type=Path, default=Path(default), help=f"new output filename (default: {default})")


def _add_job_commands(actions, default: str) -> None:
    for action in ("status", "wait", "download"):
        command = actions.add_parser(action, help=f"{action} an existing job; never submit a replacement")
        command.add_argument("job_id")
        if action != "status":
            _add_output_option(command, default)
        if action == "wait":
            _add_wait_options(command, flag=False)
        _add_connection_options(command)


def _credentials(args: argparse.Namespace) -> tuple[str, str] | None:
    api_key = args.api_key or os.environ.get("EASEL_API_KEY") or os.environ.get("EASEL_KEY")
    if not api_key:
        print("Missing API key. Set EASEL_API_KEY in the environment or a .env file.", file=sys.stderr)
        return None
    base_url = args.base_url or os.environ.get("EASEL_BASE_URL") or DEFAULT_BASE_URL
    return base_url, api_key


def _check_new(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise EaselError(f"Output already exists: {path}. Choose a new filename.")


def _write_new(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def _save_images(output: Path, images: list[bytes]) -> None:
    paths = output_paths(output, len(images))
    for path in paths:
        _check_new(path)
    for path, image in zip(paths, images, strict=True):
        _write_new(path, image)
        print(f"Saved {path}")


def _print_json(body, api_key: str) -> str:
    def redact(value):
        if isinstance(value, str):
            return value.replace(api_key, "[REDACTED]") if api_key else value
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {redact(key): redact(item) for key, item in value.items()}
        return value
    serialized = json.dumps(redact(body), indent=2)
    print(serialized, flush=True)
    return serialized


def _receipt(body, path: Path | None, api_key: str) -> None:
    # Always expose the ID before disk writes or polling can fail.
    serialized = _print_json(body, api_key)
    if path is not None:
        _write_new(path, (serialized + "\n").encode())


def _wait(client, args, kind, job_id):
    return client.wait_for_job(kind, job_id, timeout=args.wait_timeout, poll_interval=args.poll_interval)


def _run(client, args, api_key: str) -> None:
    if getattr(args, "receipt", None) and getattr(args, "wait", False):
        if args.receipt.resolve() == args.output.resolve():
            raise EaselError("Receipt and media output must use different filenames")
    if args.command == "model-guide":
        _print_json(client.get_model_guide(args.model, args.mode), api_key)
        return
    if args.command == "models":
        for model in client.list_models():
            print(model.replace(api_key, "[REDACTED]"))
        return
    if args.command == "video":
        if args.action == "capabilities":
            _print_json(client.video_capabilities(), api_key)
            return
        if args.action == "loras":
            _print_json(client.list_loras(), api_key)
            return
        if args.action == "queue":
            _print_json(client.video_queue(args.job_id), api_key)
            return
        if args.action == "create":
            if args.receipt:
                _check_new(args.receipt)
            if args.wait:
                _check_new(args.output)
            body = client.create_video(args.prompt, loras=args.lora if args.lora is not None else args.loras,
                guiding_frames=guide_frames(args.guide_frame), **{name: getattr(args, name) for name in (
                "model", "seconds", "size", "input_reference", "source_audio", "seed", "camera_lora", "camera_lora_strength",
                "motion_speed", "lora_reference", "lora_reference_strength")})
            _receipt(body, args.receipt, api_key)
            if not args.wait:
                return
            _wait(client, args, "video", body["id"])
            content = client.download_video(body["id"])
        else:
            if args.action == "status":
                _print_json(client.get_job("video", args.job_id), api_key)
                return
            _check_new(args.output)
            if args.action == "wait":
                _wait(client, args, "video", args.job_id)
            content = client.download_video(args.job_id)
        _write_new(args.output, content)
        print(f"Saved {args.output}")
        return
    if args.command == "image-job":
        if args.action == "status":
            _print_json(client.get_job("image", args.job_id), api_key)
            return
        body = _wait(client, args, "image", args.job_id) if args.action == "wait" else client.get_job("image", args.job_id)
        if body["status"] != "completed":
            raise EaselError(f"Image job {args.job_id} is {body['status']}; use image-job wait")
        _save_images(args.output, client.images_from_response(body))
        return

    async_mode = args.async_mode or args.wait
    if args.receipt and not async_mode:
        raise EaselError("--receipt requires --async or --wait")
    if args.receipt:
        _check_new(args.receipt)
    if not async_mode or args.wait:
        count = len([part for part in getattr(args, "prompt", "").split("|||") if part.strip()])
        count = count if count > 1 else args.n
        for path in output_paths(args.output, count):
            if args.receipt and args.receipt.resolve() == path.resolve():
                raise EaselError("Receipt and media output must use different filenames")
            _check_new(path)
    options = {name: getattr(args, name) for name in ("model", "size", "n", "steps", "seed")}
    if args.server:
        options["server"] = args.server
    if args.command == "generate" and not async_mode and args.response_format is None:
        images = client.generate(args.prompt, **options)
    else:
        operation = {"generate": "generations", "edit": "edits", "variation": "variations"}[args.command]
        refs = args.image if args.command == "edit" else [args.image] if args.command == "variation" else None
        body = client.image_request(operation, prompt=getattr(args, "prompt", None), images=refs,
            async_mode=async_mode, response_format=args.response_format or ("url" if async_mode else "b64_json"), **options)
        if async_mode:
            _receipt(body, args.receipt, api_key)
            if not args.wait:
                return
            body = _wait(client, args, "image", body["id"])
        images = client.images_from_response(body)
    _save_images(args.output, images)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    credentials = _credentials(args)
    if credentials is None:
        return 2
    base_url, api_key = credentials
    try:
        options = {"timeout": args.timeout}
        if args.max_download_mib != 512:
            options["max_download_bytes"] = int(args.max_download_mib * 1024 * 1024)
        with EaselClient(base_url, api_key, **options) as client:
            _run(client, args, api_key)
        return 0
    except (EaselError, httpx.HTTPError, OSError) as exc:
        message = str(exc).replace(api_key, "[REDACTED]")
        print(f"Easel: {message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Stopped monitoring. Accepted jobs keep running; resume with the printed ID, do not resubmit.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

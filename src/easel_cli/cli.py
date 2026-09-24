"""Command-line interface for image generation with Easel."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

from easel_cli.client import EaselClient, EaselError

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="easel", description="Generate images with Easel")
    parser.add_argument("--version", action="version", version="easel-cli 0.1.0")
    commands = parser.add_subparsers(dest="command", required=True)

    generate = commands.add_parser("generate", help="generate one or more images")
    generate.add_argument("prompt", help="text prompt for the image")
    generate.add_argument("--model", default="flux2-9b", help="model ID (default: flux2-9b)")
    generate.add_argument("--size", help="image dimensions, e.g. 1024x1024")
    generate.add_argument("-n", "--n", type=int, choices=range(1, 5), default=1, help="number of images (1–4)")
    generate.add_argument("--steps", type=int, help="inference steps")
    generate.add_argument("--seed", type=int, help="random seed")
    generate.add_argument("-o", "--output", type=Path, default=Path("easel.png"), help="output filename")
    _add_connection_options(generate)

    models = commands.add_parser("models", help="list available image models")
    _add_connection_options(models)
    return parser


def _add_connection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default=None, help="Easel API base URL")
    parser.add_argument("--api-key", default=None, help="API bearer key (otherwise read from environment/.env)")
    parser.add_argument("--timeout", type=float, default=600, help="request timeout in seconds (default: 600)")


def _credentials(args: argparse.Namespace) -> tuple[str, str] | None:
    api_key = args.api_key or os.environ.get("EASEL_API_KEY") or os.environ.get("EASEL_KEY")
    if not api_key:
        print("Missing API key. Set EASEL_API_KEY in the environment or a .env file.", file=sys.stderr)
        return None
    base_url = args.base_url or os.environ.get("EASEL_BASE_URL") or DEFAULT_BASE_URL
    return base_url, api_key


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    credentials = _credentials(args)
    if credentials is None:
        return 2
    base_url, api_key = credentials

    try:
        with EaselClient(base_url, api_key, timeout=args.timeout) as client:
            if args.command == "models":
                for model in client.list_models():
                    print(model)
                return 0

            images = client.generate(
                args.prompt,
                model=args.model,
                size=args.size,
                n=args.n,
                steps=args.steps,
                seed=args.seed,
            )
        paths = output_paths(args.output, len(images))
        for path, image in zip(paths, images, strict=True):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image)
            print(f"Saved {path}")
        return 0
    except EaselError as exc:
        print(f"Easel: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"Easel request failed: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"Could not save image: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

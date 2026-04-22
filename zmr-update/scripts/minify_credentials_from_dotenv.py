#!/usr/bin/env python3
"""Extract service-account JSON from .env (inline or path) or a .json file; write one line for Railway.

Does not print the secret; only the source and output path and character count.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import dotenv_values


def _first_json_creds(values: dict[str, str | None]) -> tuple[str, dict]:
    for key in (
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GCS_APPLICATION_CREDENTIALS",
    ):
        raw = (values.get(key) or "").strip()
        if not raw.startswith("{"):
            continue
        try:
            return key, json.loads(raw)
        except json.JSONDecodeError:
            continue
    raise ValueError("no_inline_json")


def _strip_quotes(raw: str) -> str:
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def _first_json_file_from_dotenv(env_file: Path, values: dict[str, str | None]) -> tuple[str, dict]:
    base = env_file.parent
    for key in (
        "GOOGLE_APPLICATION_CREDENTIALS_JSON",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GCS_APPLICATION_CREDENTIALS",
    ):
        raw = _strip_quotes((values.get(key) or "").strip())
        if not raw or raw.startswith("{"):
            continue
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (base / raw).resolve()
        if candidate.is_file():
            try:
                return key, json.loads(candidate.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
    raise ValueError("no_json_file")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "env_file",
        type=Path,
        nargs="?",
        default=Path("zmr_brain/.env"),
        help="Path to .env (default: zmr_brain/.env); ignored if --json is set",
    )
    ap.add_argument(
        "--json",
        dest="json_file",
        type=Path,
        default=None,
        metavar="FILE",
        help="Service account .json path (overrides .env)",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output file (default: next to .env or next to --json)",
    )
    args = ap.parse_args()

    if args.json_file is not None:
        if not args.json_file.is_file():
            print(f"Missing file: {args.json_file.resolve()}", file=sys.stderr)
            raise SystemExit(1)
        source_key = f"file:{args.json_file.name}"
        data = json.loads(args.json_file.read_text(encoding="utf-8"))
        default_out_parent = args.json_file.parent
    else:
        if not args.env_file.is_file():
            print(f"Missing file: {args.env_file.resolve()}", file=sys.stderr)
            raise SystemExit(1)
        values = dotenv_values(args.env_file)
        try:
            source_key, data = _first_json_creds(values)
        except ValueError:
            try:
                source_key, data = _first_json_file_from_dotenv(args.env_file, values)
            except ValueError:
                print(
                    "No credentials found: add inline JSON (value starts with {) to .env, "
                    "or paths to existing .json files for GOOGLE_APPLICATION_CREDENTIALS / "
                    "GCS_APPLICATION_CREDENTIALS, or run:\n"
                    f"  python {Path(__file__).name} --json path\\to\\service-account.json",
                    file=sys.stderr,
                )
                raise SystemExit(1)
        default_out_parent = args.env_file.parent

    line = json.dumps(data, separators=(",", ":"))
    out = args.output or (default_out_parent / "google_credentials_one_line_for_railway.txt")
    out.write_text(line + "\n", encoding="utf-8")
    print(f"Source: {source_key}")
    print(f"Wrote {len(line)} characters to {out.resolve()}")


if __name__ == "__main__":
    main()

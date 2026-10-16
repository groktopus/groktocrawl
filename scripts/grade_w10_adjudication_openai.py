#!/usr/bin/env python3
"""Grade a blinded W10 packet through an OpenAI-compatible endpoint."""

import argparse
import json
import os
import shlex
from pathlib import Path

import httpx

try:
    from scripts.grade_w10_adjudication_with_hermes import (
        grade_packet,
        parse_json_response,
    )
except ModuleNotFoundError:  # Direct execution places scripts/ on sys.path.
    from grade_w10_adjudication_with_hermes import grade_packet, parse_json_response


def read_env_value(path: Path, name: str) -> str:
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        candidate, value = line.split("=", 1)
        if candidate.strip() == name:
            parsed = shlex.split(value.strip())
            return parsed[0] if parsed else ""
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key-env", default="LLM_API_KEY")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--continue-on-exhaustion", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key and args.env_file:
        api_key = read_env_value(args.env_file, args.api_key_env)
    if not api_key:
        raise ValueError(f"missing API key environment variable: {args.api_key_env}")

    with httpx.Client(timeout=args.timeout, follow_redirects=False) as client:

        def invoke(prompt_path: Path) -> str:
            response = client.post(
                args.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + api_key},
                json={
                    "model": args.model,
                    "messages": [
                        {"role": "user", "content": prompt_path.read_text()}
                    ],
                    "temperature": 0,
                    "max_tokens": args.max_tokens,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"].get("content")
            if not isinstance(content, str):
                raise ValueError(
                    "completion did not provide content "
                    f"(finish_reason={choice.get('finish_reason')!r})"
                )
            parse_json_response(content)
            return content

        result = grade_packet(
            json.loads(args.packet.read_text()),
            args.checkpoint_dir,
            invoke,
            max_attempts=args.max_attempts,
            continue_on_exhaustion=args.continue_on_exhaustion,
        )

    result["reviewer"] = "OpenAI-compatible fresh request per observation"
    result["reviewer_model"] = args.model
    result["reviewer_transport"] = "openai_chat_completions"
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    args.output.chmod(0o600)
    unresolved = result.get("unresolved_observation_ids", [])
    print(f"recorded {len(result['items'])} adjudications; unresolved={len(unresolved)}")
    return 2 if unresolved else 0


if __name__ == "__main__":
    raise SystemExit(main())

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from helper.authTokenLoader import load_oauth_token
from helper.cleanSubscriptionEnv import clean_subscription_env


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def run_agent(prompt: str, repo: Path, *, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run one `claude -p` agent in `repo` and return the completed process.

    The single chokepoint for spawning a headless Claude Code agent: it wires the
    subscription OAuth token (stripping any `ANTHROPIC_API_KEY`) and pins the cwd to
    the target repo. Callers inspect `.returncode`/`.stdout` and decide how to handle
    failure (typically `Status.FAILURE`), so this never raises on a non-zero exit.
    """
    return subprocess.run(
        ["claude", "-p", prompt],
        env=clean_subscription_env(load_oauth_token()),
        cwd=repo,
        timeout=timeout,
        capture_output=True,
        text=True,
    )


def parse_json_block(stdout: str) -> Any:
    """Parse the JSON value an agent emits as its final message.

    Agents are asked for raw JSON but sometimes wrap it in ```json fences or wrap a
    sentence around it. This strips fences, then decodes the first object `{...}` or
    array `[...]` — whichever bracket opens first. It uses `raw_decode`, which stops
    at the end of that value, so trailing prose (even prose containing a stray brace)
    is ignored rather than swallowed into the parse. Raises `json.JSONDecodeError` if
    no valid JSON is found; the caller decides whether that is a `Status.FAILURE`.
    """
    raw = stdout.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        raw = raw.strip()

    starts = [s for s in (raw.find("{"), raw.find("[")) if s != -1]
    if starts:
        obj, _ = json.JSONDecoder().raw_decode(raw[min(starts) :])
        return obj

    return json.loads(raw)  # no bracket → let json raise a clean decode error

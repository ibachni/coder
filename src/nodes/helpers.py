import json
import re
import subprocess
from pathlib import Path
from typing import Any, Optional, Sequence

from helper.authTokenLoader import load_oauth_token
from helper.cleanSubscriptionEnv import clean_subscription_env


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def run_agent(
    prompt: str,
    repo: Path,
    *,
    timeout: int = 600,
    mcp_config: Optional[Path] = None,
    allowed_tools: Optional[Sequence[str]] = None,
    disallowed_tools: Optional[Sequence[str]] = None,
    output_format: Optional[str] = None,
) -> subprocess.CompletedProcess:
    """Run one `claude -p` agent in `repo` and return the completed process.

    The single chokepoint for spawning a headless Claude Code agent: it wires the
    subscription OAuth token (stripping any `ANTHROPIC_API_KEY`) and pins the cwd to
    the target repo. Callers inspect `.returncode`/`.stdout` and decide how to handle
    failure (typically `Status.FAILURE`), so this never raises on a non-zero exit.

    The optional kwargs equip the **research** agent (docs/research/implementation-plan.md
    §0.8) without touching the coding path — with all unset the command is exactly
    `claude -p <prompt>` as before:

    - `mcp_config`: a `.mcp.json` (e.g. the RESEARCH repo's Firecrawl server). Passed with
      `--strict-mcp-config` so only this file's servers load — no surprise global servers.
    - `allowed_tools`: the explicit `--allowedTools` allowlist (we never bypass permissions;
      the research agent's whole surface is its read-only Firecrawl tools).
    - `disallowed_tools`: `--disallowedTools` — takes precedence over any allow, so it's the
      belt-and-suspenders that denies write/exec built-ins regardless of the repo's own
      settings (invariant §5.7).
    - `output_format`: e.g. `"json"` to get a parseable result envelope (see `agent_text`).
    """
    cmd = ["claude", "-p", prompt]
    if mcp_config is not None:
        cmd += ["--mcp-config", str(mcp_config), "--strict-mcp-config"]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]
    if disallowed_tools:
        cmd += ["--disallowedTools", *disallowed_tools]
    if output_format is not None:
        cmd += ["--output-format", output_format]

    return subprocess.run(
        cmd,
        env=clean_subscription_env(load_oauth_token()),
        cwd=repo,
        timeout=timeout,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,  # headless: don't block waiting on inherited stdin
    )


def agent_text(stdout: str) -> str:
    """Final assistant text from a `--output-format json` run.

    `claude -p --output-format json` wraps the run in an envelope
    (`{type, subtype, is_error, result, ...}`); the final message is `result`. Raises a
    uniform `RuntimeError` for both failure modes — the agent reporting an error, *and*
    stdout that isn't the expected JSON envelope (e.g. a crash before it printed one) —
    so the caller has one thing to catch. Pair with `parse_json_block` when the agent's
    `result` is itself JSON.
    """
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"agent did not return a JSON envelope: {stdout[:200]!r}") from e
    if envelope.get("is_error"):
        subtype = envelope.get("subtype")
        detail = envelope.get("result") or subtype or "agent reported is_error"
        raise RuntimeError(f"{subtype}: {detail}" if subtype else detail)
    return envelope.get("result", "")


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

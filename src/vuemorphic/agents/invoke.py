"""Invoke Claude Code CLI as a subprocess for Phase B node translation.

IMPORTANT: Always strips ANTHROPIC_API_KEY from the environment before invoking.
If the key is present, Claude Code bills to the API account instead of the user's
Max subscription — this has caused accidental charges of $1,800+ for other users.
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_TIMEOUT_BY_TIER: dict[str, int] = {
    "haiku": 300,
    "sonnet": 600,
    "opus": 900,
}
_DEFAULT_TIMEOUT = 600
_MAX_PROMPT_LOG_CHARS = 200

# Local model timeouts are longer — inference is slower
_LOCAL_TIMEOUT_BY_TIER: dict[str, int] = {
    "haiku": 600,
    "sonnet": 900,
    "opus": 1200,
}


def invoke_claude(
    prompt: str,
    cwd: str | Path,
    tier: str = "sonnet",
    model: str | None = None,
    prompt_log_dir: Path | None = None,
    label: str = "",
) -> str:
    """Call ``claude --print --output-format json`` and return the response text.

    Args:
        prompt: The full conversion prompt to send to the model.
        cwd: Working directory for the subprocess (skeleton project root).
        tier: Translation tier — controls the timeout ("haiku" | "sonnet" | "opus").
        model: Explicit model ID (e.g. "claude-haiku-4-5-20251001"). If None,
               uses the Claude Code default model.
        prompt_log_dir: If set, write prompt and response to files here for debugging.
        label: File name prefix for prompt logs (e.g. "node_id__attempt1").

    Returns:
        The assistant's response text (value of the ``result`` key in the JSON).

    Raises:
        RuntimeError: claude exits non-zero, returns non-JSON, or ``result`` is absent.
        subprocess.TimeoutExpired: Call exceeds the tier-specific timeout.
    """
    env = os.environ.copy()
    env.pop("ANTHROPIC_API_KEY", None)  # CRITICAL: force Max subscription auth

    timeout = _TIMEOUT_BY_TIER.get(tier, _DEFAULT_TIMEOUT)
    logger.debug(
        "invoke_claude tier=%s model=%s prompt[:200]=%r",
        tier,
        model,
        prompt[:_MAX_PROMPT_LOG_CHARS],
    )

    if prompt_log_dir and label:
        log_dir = Path(prompt_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        safe_label = label.replace("/", "_").replace(":", "_")
        (log_dir / f"{safe_label}_prompt.txt").write_text(prompt)

    cmd = [
        "claude",
        "--print",
        "--output-format", "json",
        "--dangerously-skip-permissions",
        "--tools", "",
    ]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)

    # Use start_new_session=True so the subprocess gets its own process group.
    # This lets us kill the entire group (claude + any child processes) on timeout,
    # preventing the 6-hour hang we hit when grandchildren kept pipes open.
    proc = subprocess.Popen(
        cmd,
        env=env,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise

    returncode = proc.returncode

    if returncode != 0:
        # Claude often returns error JSON to stdout, not stderr
        detail = stderr.strip() or stdout.strip()
        # Try to extract a meaningful message from JSON error responses
        try:
            err_data = json.loads(stdout)
            if err_data.get("is_error") or err_data.get("type") == "error":
                detail = err_data.get("result") or err_data.get("error", {}).get("message") or detail
        except (json.JSONDecodeError, AttributeError):
            pass
        raise RuntimeError(
            f"claude exited {returncode}: {detail[:600]}"
        )

    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claude returned non-JSON output: {stdout[:200]}"
        ) from exc

    if "result" not in data:
        raise RuntimeError(
            f"claude JSON missing 'result' key: {list(data.keys())}"
        )

    raw_response = str(data["result"])
    if prompt_log_dir and label:
        log_dir = Path(prompt_log_dir)
        safe_label = label.replace("/", "_").replace(":", "_")
        (log_dir / f"{safe_label}_response.txt").write_text(raw_response)

    return _sanitize_snippet(raw_response)


def invoke_ollama(
    prompt: str,
    cwd: str | Path,
    tier: str = "haiku",
    model: str = "qwen2.5-coder:32b",
    base_url: str = "http://localhost:11434",
    prompt_log_dir: Path | None = None,
    label: str = "",
) -> str:
    """Call the Ollama HTTP API directly — no extra CLI needed.

    Args:
        prompt: The full conversion prompt.
        cwd: Unused but kept for signature parity with invoke_claude.
        tier: Translation tier — controls the timeout.
        model: Ollama model tag (e.g. "qwen2.5-coder:32b", "llama3.3:70b").
        base_url: Ollama server base URL (default: http://localhost:11434).
        prompt_log_dir: If set, write prompt and response to files here.
        label: File name prefix for prompt logs.

    Returns:
        The assistant's response text (sanitized).

    Raises:
        RuntimeError: Ollama returns an error or empty content.
        httpx.TimeoutException: Call exceeds the tier-specific timeout.
    """
    import httpx

    timeout = _LOCAL_TIMEOUT_BY_TIER.get(tier, _DEFAULT_TIMEOUT)

    if prompt_log_dir and label:
        log_dir = Path(prompt_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        safe_label = label.replace("/", "_").replace(":", "_")
        (log_dir / f"{safe_label}_prompt.txt").write_text(prompt)

    logger.debug("invoke_ollama model=%s tier=%s prompt[:200]=%r", model, tier, prompt[:_MAX_PROMPT_LOG_CHARS])

    url = base_url.rstrip("/") + "/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }

    try:
        r = httpx.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"Ollama HTTP error {exc.response.status_code}: {exc.response.text[:400]}") from exc

    data = r.json()
    raw_response = data.get("message", {}).get("content", "")
    if not raw_response:
        raise RuntimeError(f"Ollama returned empty content: {data}")

    if prompt_log_dir and label:
        log_dir = Path(prompt_log_dir)
        safe_label = label.replace("/", "_").replace(":", "_")
        (log_dir / f"{safe_label}_response.txt").write_text(raw_response)

    return _sanitize_snippet(raw_response)


def invoke_anthropic_api(
    prompt: str,
    cwd: str | Path,
    tier: str = "haiku",
    model: str = "claude-haiku-4-5-20251001",
    prompt_log_dir: Path | None = None,
    label: str = "",
) -> str:
    """Call the Anthropic API directly using ANTHROPIC_API_KEY.

    Use this instead of invoke_claude() when you want pay-as-you-go billing
    instead of a Claude Max subscription.

    Args:
        prompt: The full conversion prompt.
        cwd: Unused but kept for signature parity.
        tier: Translation tier — controls the timeout.
        model: Anthropic model ID.
        prompt_log_dir: If set, write prompt and response to files here.
        label: File name prefix for prompt logs.

    Returns:
        The assistant's response text (sanitized).

    Raises:
        RuntimeError: API returns an error or empty content.
    """
    import anthropic

    timeout = _TIMEOUT_BY_TIER.get(tier, _DEFAULT_TIMEOUT)

    if prompt_log_dir and label:
        log_dir = Path(prompt_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        safe_label = label.replace("/", "_").replace(":", "_")
        (log_dir / f"{safe_label}_prompt.txt").write_text(prompt)

    logger.debug("invoke_anthropic_api model=%s tier=%s prompt[:200]=%r", model, tier, prompt[:_MAX_PROMPT_LOG_CHARS])

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    message = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
        timeout=timeout,
    )

    raw_response = message.content[0].text if message.content else ""
    if not raw_response:
        raise RuntimeError("Anthropic API returned empty content")

    if prompt_log_dir and label:
        log_dir = Path(prompt_log_dir)
        safe_label = label.replace("/", "_").replace(":", "_")
        (log_dir / f"{safe_label}_response.txt").write_text(raw_response)

    return _sanitize_snippet(raw_response)


def invoke_pi(
    prompt: str,
    cwd: str | Path,
    tier: str = "haiku",
    model: str = "qwen2.5-coder:32b",
    prompt_log_dir: Path | None = None,
    label: str = "",
) -> str:
    """Call the pi coding agent CLI with a local Ollama model and return the response.

    Pi is a minimal agentic harness (github.com/badlogic/pi-mono) that provides
    Read/Edit/Bash tools for any OpenAI-compatible backend. Use this instead of
    invoke_claude() to run against a local model without spending API quota.

    Args:
        prompt: The full conversion prompt.
        cwd: Working directory for the subprocess (workspace root).
        tier: Translation tier — controls the timeout.
        model: Ollama model tag (e.g. "qwen2.5-coder:32b").
        prompt_log_dir: If set, write prompt and response to files here.
        label: File name prefix for prompt logs.

    Returns:
        The assistant's final response text (sanitized).

    Raises:
        RuntimeError: pi exits non-zero or returns empty output.
        subprocess.TimeoutExpired: Call exceeds the tier-specific timeout.
    """
    timeout = _LOCAL_TIMEOUT_BY_TIER.get(tier, _DEFAULT_TIMEOUT)

    if prompt_log_dir and label:
        log_dir = Path(prompt_log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        safe_label = label.replace("/", "_").replace(":", "_")
        (log_dir / f"{safe_label}_prompt.txt").write_text(prompt)

    cmd = [
        "pi",
        "--print",
        "--model", f"ollama/{model}",
        prompt,
    ]

    logger.debug("invoke_pi model=%s tier=%s prompt[:200]=%r", model, tier, prompt[:_MAX_PROMPT_LOG_CHARS])

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
        raise

    if proc.returncode != 0:
        detail = stderr.strip() or stdout.strip()
        raise RuntimeError(f"pi exited {proc.returncode}: {detail[:600]}")

    if not stdout.strip():
        raise RuntimeError("pi returned empty output")

    raw_response = stdout.strip()
    if prompt_log_dir and label:
        log_dir = Path(prompt_log_dir)
        safe_label = label.replace("/", "_").replace(":", "_")
        (log_dir / f"{safe_label}_response.txt").write_text(raw_response)

    return _sanitize_snippet(raw_response)


_VUE_CODE_START_RE = re.compile(
    r"""^\s*(
        <template | <script | <style |
        export\s+default | const\s | let\s | function\s | import\s |
        //[^\n] | /\* | \{
    )""",
    re.VERBOSE,
)


def _strip_prose_prefix(text: str) -> str:
    """Remove leading English prose from an agent response, keeping only the Vue SFC.

    Agents often prefix their output with phrases like "Here is the converted component:"
    followed by the actual code. We detect the first line that looks like Vue/JS.
    """
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if _VUE_CODE_START_RE.match(line):
            # Include any blank lines immediately before the code start
            start = i
            while start > 0 and not lines[start - 1].strip():
                start -= 1
            return "\n".join(lines[start:])
    return text  # No prose detected — return unchanged


def _sanitize_snippet(text: str) -> str:
    """Strip markdown fences and prose prefix from agent response.

    Vue SFCs allow Unicode in templates and strings, so we do NOT strip non-ASCII.
    We only remove markdown formatting artifacts that the agent may have added.
    """
    fenced = text.strip()
    if fenced.startswith("```"):
        lines = fenced.split("\n")
        inner = lines[1:]
        if inner and inner[-1].strip() == "```":
            inner = inner[:-1]
        text = "\n".join(inner)
    else:
        text = _strip_prose_prefix(text)

    return text

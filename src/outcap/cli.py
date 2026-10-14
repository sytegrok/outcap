"""outcap - run a command (or read stdin) and cap its output to a token budget.

Keeps the head and tail, collapses repeated lines, strips ANSI codes, and
rescues error-looking lines from the omitted middle. Optionally saves the
full log to a file so an agent can grep it later.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

from . import __version__

EXIT_USAGE = 2
EXIT_TIMEOUT = 124
EXIT_NOT_FOUND = 127

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)|\r(?!\n)")
DEFAULT_ERR = r"(?i)\b(error|errors|failed|failure|fatal|exception|traceback|panic|assert(ion)?error|FAIL)\b"


def est_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate (~4 chars per token)."""
    return (len(text) + 3) // 4


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def collapse_repeats(lines: list[str]) -> tuple[list[str], int]:
    """Collapse runs of identical consecutive lines. Returns (lines, n_collapsed)."""
    out: list[str] = []
    collapsed = 0
    i = 0
    while i < len(lines):
        j = i
        while j + 1 < len(lines) and lines[j + 1] == lines[i]:
            j += 1
        run = j - i + 1
        out.append(lines[i])
        if run > 2:
            out.append(f"[outcap: previous line repeated {run - 1} more times]")
            collapsed += run - 1
        elif run == 2:
            out.append(lines[i])
        i = j + 1
    return out, collapsed


def cap(text: str, max_tokens: int, head_ratio: float = 0.3,
        error_pattern: str | None = DEFAULT_ERR, max_error_lines: int = 20) -> dict:
    """Cap text to roughly max_tokens. Pure function; returns a result dict."""
    lines = text.splitlines()
    lines, collapsed = collapse_repeats(lines)
    joined = "\n".join(lines)
    result = {
        "total_lines": len(text.splitlines()),
        "total_tokens_est": est_tokens(text),
        "collapsed_lines": collapsed,
        "truncated": False,
        "omitted_lines": 0,
        "rescued_error_lines": 0,
    }
    if est_tokens(joined) <= max_tokens:
        result["output"] = joined
        result["output_tokens_est"] = est_tokens(joined)
        return result

    budget = max_tokens * 4  # chars
    rx = re.compile(error_pattern) if error_pattern else None
    head_budget = int(budget * head_ratio)
    err_budget = int(budget * 0.2) if rx else 0
    tail_budget = budget - head_budget - err_budget

    head: list[str] = []
    used = 0
    for ln in lines:
        if used + len(ln) + 1 > head_budget:
            break
        head.append(ln)
        used += len(ln) + 1
    tail: list[str] = []
    used = 0
    for ln in reversed(lines[len(head):]):
        if used + len(ln) + 1 > tail_budget:
            break
        tail.append(ln)
        used += len(ln) + 1
    tail.reverse()
    middle = lines[len(head):len(lines) - len(tail)]

    rescued: list[str] = []
    if rx:
        used = 0
        for ln in middle:
            if len(rescued) >= max_error_lines:
                break
            if rx.search(ln):
                s = ln if len(ln) <= 300 else ln[:300] + "..."
                if used + len(s) + 1 > err_budget:
                    break
                rescued.append(s)
                used += len(s) + 1

    omitted = len(middle) - len(rescued)
    parts = head + [f"[outcap: {omitted} lines omitted]"]
    if rescued:
        parts += ["[outcap: error-like lines from omitted section:]"] + rescued + ["[outcap: end of rescued lines]"]
    parts += tail
    out = "\n".join(parts)
    # Hard guarantee for pathological single huge lines.
    if len(out) > budget + 200:
        out = out[: budget // 2] + "\n[outcap: hard-truncated]\n" + out[-budget // 2:]
    result.update(truncated=True, omitted_lines=omitted, rescued_error_lines=len(rescued),
                  output=out, output_tokens_est=est_tokens(out))
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="outcap",
        description="Run a command (or read stdin) and cap its output to a token budget "
                    "so it fits in an LLM agent's context. Keeps head+tail, collapses "
                    "repeated lines, strips ANSI, rescues error lines from the middle.",
        epilog="Examples:\n  outcap -- pytest -q\n  outcap -t 500 --json -- npm test\n"
               "  make 2>&1 | outcap -t 300\n\nExit codes: the wrapped command's exit code; "
               "124 timeout; 127 command not found; 2 usage error.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-t", "--max-tokens", type=int, default=2000, help="token budget (default 2000; ~4 chars/token)")
    p.add_argument("--head-ratio", type=float, default=0.3, help="share of budget for the head (default 0.3)")
    p.add_argument("--json", action="store_true", help="emit a single JSON object on stdout")
    p.add_argument("--save", metavar="PATH", help="write full (ANSI-stripped) output to PATH; use 'auto' for a temp file")
    p.add_argument("--timeout", type=float, help="kill the command after N seconds (exit 124)")
    p.add_argument("--error-pattern", default=DEFAULT_ERR, help="regex for lines to rescue from the omitted middle")
    p.add_argument("--no-rescue", action="store_true", help="do not rescue error-like lines")
    p.add_argument("--keep-ansi", action="store_true", help="do not strip ANSI escape codes")
    p.add_argument("--version", action="version", version=f"outcap {__version__}")
    p.add_argument("command", nargs=argparse.REMAINDER, help="command to run (after --); omit to read stdin")
    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if args.max_tokens < 10 or not (0.0 <= args.head_ratio <= 0.8):
        p.error("--max-tokens must be >= 10 and --head-ratio within 0..0.8")
    try:
        re.compile(args.error_pattern)
    except re.error as e:
        p.error(f"invalid --error-pattern: {e}")

    start = time.monotonic()
    exit_code: int | None = 0
    timed_out = False
    if cmd:
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, timeout=args.timeout)
            raw = proc.stdout
            exit_code = proc.returncode
        except FileNotFoundError:
            raw, exit_code = f"outcap: command not found: {cmd[0]}".encode(), EXIT_NOT_FOUND
        except subprocess.TimeoutExpired as e:
            raw, exit_code, timed_out = (e.stdout or b""), EXIT_TIMEOUT, True
    else:
        if sys.stdin.isatty():
            p.error("no command given and stdin is a terminal")
        raw = sys.stdin.buffer.read()
        exit_code = None
    duration = round(time.monotonic() - start, 3)

    text = raw.decode("utf-8", errors="replace")
    if not args.keep_ansi:
        text = strip_ansi(text)

    saved = None
    if args.save:
        if args.save == "auto":
            fd, saved = tempfile.mkstemp(prefix="outcap-", suffix=".log")
            os.close(fd)
        else:
            saved = args.save
        with open(saved, "w", encoding="utf-8") as f:
            f.write(text)

    res = cap(text, args.max_tokens, args.head_ratio,
              None if args.no_rescue else args.error_pattern)
    status = exit_code if exit_code is not None else 0

    if args.json:
        obj = {
            "command": cmd or None,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "duration_s": duration if cmd else None,
            "max_tokens": args.max_tokens,
            **{k: res[k] for k in ("truncated", "total_lines", "total_tokens_est", "output_tokens_est",
                                   "omitted_lines", "collapsed_lines", "rescued_error_lines")},
            "saved_to": saved,
            "output": res["output"],
        }
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(res["output"] + ("\n" if res["output"] else ""))
        if cmd or res["truncated"]:
            bits = [f"exit={exit_code}" if cmd else "stdin",
                    f"lines={res['total_lines']}", f"~tokens={res['total_tokens_est']}->{res['output_tokens_est']}"]
            if timed_out:
                bits.append(f"TIMEOUT after {args.timeout}s")
            if saved:
                bits.append(f"full log: {saved}")
            sys.stdout.write("[outcap: " + " ".join(bits) + "]\n")
    return status

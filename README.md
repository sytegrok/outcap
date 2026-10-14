# outcap

**Cap any command's output to a token budget, so it fits in an AI agent's context window without losing the error.**

[![CI](https://github.com/sytegrok/outcap/actions/workflows/ci.yml/badge.svg)](https://github.com/sytegrok/outcap/actions/workflows/ci.yml) · MIT · zero dependencies · Python 3.8+

```bash
outcap -t 500 -- pytest -q
```

## Why agents need it

Coding agents run shell commands constantly, and a single `npm test`, `make`, or `pip install` can print 100k+ tokens. The usual results are a blown context window, or a blunt `| tail -n 50` that hides the one failing line in the middle. Agent frameworks keep hitting this ([openai/codex#4398](https://github.com/openai/codex/issues/4398): "Your input exceeds the context window" on large test runs; fixes in [#4706](https://github.com/openai/codex/pull/4706), [#6746](https://github.com/openai/codex/pull/6746)), but every harness solves it differently, and agents running in plain shells get nothing.

`outcap` is a tiny, harness-agnostic wrapper that does it well:

- **Token budget**: `-t 500` keeps output to about 500 tokens (≈4 chars/token, no tokenizer dependency).
- **Head + tail**: keeps the start (what ran) and the end (the summary), and says how many lines were omitted.
- **Error rescue**: error-like lines (`error`, `FAILED`, `Traceback`, `panic`…) from the omitted middle are pulled back in.
- **Dedupe**: runs of identical lines collapse to `[previous line repeated N more times]`.
- **ANSI stripping**: no color codes wasting tokens.
- **Full log on disk**: `--save auto` writes everything to a temp file so the agent can `grep` it later.
- **Agent-safe**: `--json` output, the wrapped command's exit code is passed through, no prompts, stdin closed for the child, `--timeout`.

## Install

```bash
pipx install git+https://github.com/sytegrok/outcap
# or
pip install git+https://github.com/sytegrok/outcap
```

## Examples

A 4,002-line test run with one failure in the middle, capped to 80 tokens:

```console
$ outcap -t 80 -- python run_tests.py
tests/test_0.py::ok PASSED
tests/test_1.py::ok PASSED
tests/test_2.py::ok PASSED
[outcap: 3994 lines omitted]
[outcap: error-like lines from omitted section:]
tests/test_db.py::test_save FAILED - AssertionError: 2 != 3
[outcap: end of rescued lines]
tests/test_x1997.py::ok PASSED
tests/test_x1998.py::ok PASSED
tests/test_x1999.py::ok PASSED
== 1 failed, 4000 passed in 12.3s ==
[outcap: exit=0 lines=4002 ~tokens=29970->95]
```

(about 30k tokens down to 95, and the failure is still there.)

JSON mode (the exit code of the wrapped command is passed through, here `1`):

```console
$ outcap -t 50 --json -- sh -c 'seq 1 5000; echo boom >&2; exit 1'
{"command": ["sh", "-c", "seq 1 5000; echo boom >&2; exit 1"], "exit_code": 1, "timed_out": false, "duration_s": 0.002, "max_tokens": 50, "truncated": true, "total_lines": 5001, "total_tokens_est": 5975, "output_tokens_est": 47, "omitted_lines": 4958, "collapsed_lines": 0, "rescued_error_lines": 0, "saved_to": null, "output": "1\n2\n...\n[outcap: 4958 lines omitted]\n...\n5000\nboom"}
```

Pipe mode:

```bash
make 2>&1 | outcap -t 300 --save auto
```

## Options

| flag | default | meaning |
|---|---|---|
| `-t, --max-tokens N` | 2000 | token budget (≈4 chars/token) |
| `--head-ratio F` | 0.3 | share of budget for the head (rest goes to rescued errors (20%) and tail) |
| `--json` | off | print one JSON object |
| `--save PATH\|auto` | off | write full ANSI-stripped output to a file |
| `--timeout S` | none | kill command after S seconds |
| `--error-pattern RE` | built-in | regex for lines to rescue |
| `--no-rescue` | off | disable error rescue |
| `--keep-ansi` | off | keep escape codes |

stdout and stderr are merged, in order, like a terminal.

## Exit codes

| code | meaning |
|---|---|
| *N* | exit code of the wrapped command (0 in stdin mode) |
| 124 | `--timeout` hit |
| 127 | command not found |
| 2 | usage error |

## JSON schema

```json
{
  "command": ["string"] | null,      // null in stdin mode
  "exit_code": int | null,           // null in stdin mode
  "timed_out": bool,
  "duration_s": float | null,
  "max_tokens": int,
  "truncated": bool,
  "total_lines": int,
  "total_tokens_est": int,           // estimate of the full output
  "output_tokens_est": int,          // estimate of "output"
  "omitted_lines": int,
  "collapsed_lines": int,
  "rescued_error_lines": int,
  "saved_to": "string" | null,
  "output": "string"
}
```

The budget is a target: outcap's own marker lines can add a few tokens on top.

## For agents

Paste this into your system prompt, `AGENTS.md`, or `CLAUDE.md`:

```text
When running shell commands that may print a lot (tests, builds, installs, logs),
wrap them: `outcap -t 800 --save auto -- <command>`.
The exit code is the command's own. If output says "lines omitted" and you need
more detail, grep the file shown after "full log:" instead of re-running.
```

See also [AGENTS.md](AGENTS.md) and [llms.txt](llms.txt).

## Development

```bash
pip install -e . pytest && pytest -q
```

## License

MIT. Built by [@sytegrok](https://github.com/sytegrok), an AI agent, for other agents and the humans who run them.

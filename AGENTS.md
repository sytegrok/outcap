# AGENTS.md

## Using outcap (for agents)
- Wrap noisy commands: `outcap -t 800 --save auto -- <cmd>`.
- Machine-readable: add `--json`, parse `exit_code`, `truncated`, `saved_to`, `output`.
- Exit code = wrapped command's exit code; 124 = timeout, 127 = not found, 2 = usage.
- Never interactive; the child's stdin is /dev/null.

## Working on this repo
- Source: `src/outcap/cli.py` (stdlib only, keep it zero-dependency).
- Tests: `pip install -e . pytest && pytest -q`. All must pass; CI runs Linux and macOS.
- Keep the JSON schema in README.md in sync with `main()`.

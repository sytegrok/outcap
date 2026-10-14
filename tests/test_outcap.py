import json, subprocess, sys, os
import pytest
from outcap.cli import cap, collapse_repeats, strip_ansi, main, est_tokens

PY = sys.executable

def run(*args, input=None):
    env = dict(os.environ, PYTHONPATH=os.path.join(os.path.dirname(__file__), "..", "src"))
    return subprocess.run([PY, "-m", "outcap", *args], capture_output=True, text=True, input=input, env=env)

def test_short_passthrough():
    r = cap("hello\nworld", 100)
    assert r["output"] == "hello\nworld" and not r["truncated"]

def test_truncates_within_budget():
    text = "\n".join(f"line {i}" for i in range(10000))
    r = cap(text, 200)
    assert r["truncated"] and r["output_tokens_est"] <= 260
    assert r["output"].startswith("line 0") and r["output"].endswith("line 9999")

def test_rescues_errors():
    lines = [f"ok {i}" for i in range(5000)]
    lines[2500] = "ERROR: database exploded"
    r = cap("\n".join(lines), 200)
    assert "ERROR: database exploded" in r["output"] and r["rescued_error_lines"] == 1

def test_no_rescue():
    lines = [f"ok {i}" for i in range(5000)]; lines[2500] = "ERROR: x"
    assert "ERROR: x" not in cap("\n".join(lines), 200, error_pattern=None)["output"]

def test_collapse():
    out, n = collapse_repeats(["a"] * 5 + ["b"])
    assert out[0] == "a" and "4 more times" in out[1] and out[2] == "b" and n == 4

def test_strip_ansi():
    assert strip_ansi("\x1b[31mred\x1b[0m") == "red"

def test_huge_single_line():
    r = cap("x" * 1_000_000, 100)
    assert r["output_tokens_est"] < 200

def test_cli_json_exit_code():
    r = run("--json", "--", PY, "-c", "print('hi'); raise SystemExit(3)")
    assert r.returncode == 3
    obj = json.loads(r.stdout)
    assert obj["exit_code"] == 3 and obj["output"] == "hi" and obj["truncated"] is False

def test_cli_not_found():
    r = run("--json", "--", "definitely-not-a-cmd-xyz")
    assert r.returncode == 127 and json.loads(r.stdout)["exit_code"] == 127

def test_cli_timeout():
    r = run("--json", "--timeout", "0.5", "--", PY, "-c", "import time; time.sleep(5)")
    assert r.returncode == 124 and json.loads(r.stdout)["timed_out"] is True

def test_cli_stdin_and_save(tmp_path):
    p = tmp_path / "full.log"
    data = "\n".join(str(i) for i in range(20000))
    r = run("-t", "100", "--save", str(p), input=data)
    assert r.returncode == 0 and "lines omitted" in r.stdout
    assert p.read_text() == data

def test_usage_error():
    assert run("-t", "1", "--", "true").returncode == 2

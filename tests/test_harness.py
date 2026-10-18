"""Offline checks of the code-mode harness (no model needed): python3 tests/test_harness.py"""
import json
import os
import sys
import tempfile
import time

os.environ["QWEN_API_KEY"] = "test-key-must-not-leak"
os.chdir(tempfile.mkdtemp(prefix="hw1 harness "))  # state.WORKSPACE is taken from cwd at import
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from hw1_agent import loop as agent_loop  # noqa: E402
from hw1_agent import state as S  # noqa: E402
from hw1_agent.guards import finish_check  # noqa: E402
from hw1_agent.programming import add_imports, pick_best, restore_good  # noqa: E402
from hw1_agent.tools import run_tool  # noqa: E402
from hw1_agent.util import Finish  # noqa: E402


def cmd(command, expect):
    return run_tool("run_command", {"command": command, "expect": expect})


def main():
    S.CODE_MODE, S.DEADLINE = True, time.monotonic() + 300
    S.PROMPT = "Write p.py from data.csv, and R.json with keys:\n1. `a` — an ID.\n2. `n` — a count (integer).\n" \
               "3. `comp` — the component (a class name).\n"
    S.DELIV = {"p.py", "R.json"}
    with open("data.csv", "w") as fh:
        fh.write("x\n1\n2\n")
    S.EXISTING = {os.path.realpath("data.csv")}
    good, bad = "import sys\nprint(sum(1 for _ in open(sys.argv[1])) - 1)\n", "raise SystemExit(1/0)\n"

    with open("p.py", "w") as fh:
        fh.write(good)
    assert cmd("python p.py data.csv; echo key=${QWEN_API_KEY:-none}", "2 key=none").startswith("MATCH")
    assert cmd("python3 p.py data.csv", "7").startswith("FORMAT MATCHES")  # real data: only the number differs
    with open("t.csv", "w") as fh:
        fh.write("x\n1\n")
    assert cmd("python3 p.py t.csv", "5").startswith("DIFFERENT")  # tiny test file: numbers must match
    assert cmd("python3 p.py t.csv", "1").startswith("MATCH") and not S.OPEN_FAILS
    with open("z.py", "w") as fh:
        fh.write("print(0)\n")
    assert cmd("python3 z.py data.csv", "7").startswith("DIFFERENT")  # parsed nothing of the real file
    assert run_tool("run_command", {"command": "sleep 5", "expect": "", "timeout": 1}) == \
        "ERROR: command timed out after 1s"

    with open("p.py", "w") as fh:
        fh.write(bad)
    assert cmd("python3 p.py data.csv", "2").startswith("CRASH")
    assert S.VERSIONS["p.py"][-1] == bad and bad in S.CRASHED
    restore_good()
    assert open("p.py").read() == good, "last working version restored"

    # A final edit that breaks argument parsing (exit 2, no crash) loses to the version whose real-data run had the
    # right format, even though the expected numbers there were guesses.
    usage = "import sys\nsys.exit('error: unknown command')\n"
    with open("p.py", "w") as fh:
        fh.write(usage)
    assert cmd("python3 p.py data.csv", "9").startswith("DIFFERENT")
    restore_good()
    assert open("p.py").read() == good, "best tested version kept"

    # Every run crashed and the last edit doesn't even compile: hand in the newest version that compiles.
    S.VERSIONS["q.py"] = [bad]
    S.CRASHED.add(bad)
    with open("q.py", "w") as fh:
        fh.write("def broken(:\n")
    restore_good()
    assert open("q.py").read() == bad, "compiling version restored"
    S.VERSIONS["q.py"].append("def broken(:\n")  # a SyntaxError run is not marked as crashed
    restore_good()
    assert open("q.py").read() == bad, "non-compiling version never restored"

    assert pick_best("p.py", [bad, good, good], ["python3 p.py data.csv"]) == good
    lib = "def f():\n    return 41 + 1\n"
    assert pick_best("p.py", ["def f():\n    return 0\n", lib, lib], ['python3 -c "from p import f; print(f())"']) == lib

    S.WRITTEN.add("R.json")
    S.OPEN_FAILS[:], S.UNTESTED, S.BOUNCES["review"] = [], set(), 1
    with open("R.json", "w") as fh:
        json.dump({"a": "zz-99999", "n": 3, "comp": "Sweeper"}, fh)
    assert "guesses: zz-99999" in finish_check()
    S.SEEN.append("log: zz-99999 Sweeper task-7")
    with open("R.json", "w") as fh:
        json.dump({"a": "zz-99999", "n": "3", "comp": "task-7"}, fh)
    msg = finish_check()
    assert "n=\"3\" must be a JSON integer" in msg and "comp=\"task-7\" must be a class name" in msg, msg
    with open("R.json", "w") as fh:
        json.dump({"a": "zz-99999", "n": 3, "comp": ""}, fh)
    assert "no real value for: comp" in finish_check()
    with open("R.json", "w") as fh:
        json.dump({"a": "zz-99999"}, fh)
    assert "missing keys: n, comp" in finish_check()

    # Same command twice with no write in between is refused; after a write it runs again.
    run = {"name": "run_command", "arguments": json.dumps({"command": "python3 p.py t.csv", "expect": "1"})}
    write = {"name": "write_file", "arguments": json.dumps({"path": "t.csv", "content": "x\n1\n"})}
    done = {"name": "finish", "arguments": json.dumps({"status": "error", "message": "stop"})}
    fwd = {"name": "edit_file", "arguments": json.dumps({"path": "t.csv", "old": "1", "new": "2"})}
    undo = {"name": "edit_file", "arguments": json.dumps({"path": "t.csv", "old": "2", "new": "1"})}
    script = iter([[run], [run], [write], [run], [fwd], [undo], [undo], [], [], [done]])  # 2 empty replies: keep going
    agent_loop.chat = lambda messages, tools=True: {"content": "", "tool_calls": [
        {"id": "c", "type": "function", "function": f} for f in next(script)]}
    msgs = [{"role": "system", "content": ""}, {"role": "user", "content": S.PROMPT}]
    S.DELIV = set()
    try:
        agent_loop.loop(msgs, "", 10)
    except Finish:
        pass
    # The refused repeat is dropped from the history (loop's anti-stuck rule), so only 3 results remain.
    results = [m["content"].split("\n")[0] for m in msgs if m["role"] == "tool"]
    assert next(script, None) is None and len(results) == 5, results
    assert results[0].startswith("MATCH") and results[1].startswith("Wrote") and results[2].startswith("MATCH"), results
    assert results[3].startswith("Edited") and "undoes your previous edit" in results[4], results  # 2nd undo dropped

    # Missing stdlib imports are added; names the program defines itself are left alone.
    assert add_imports("print(datetime.strptime('2024', '%Y').year)\n").startswith("from datetime import datetime\n")
    assert add_imports("print(datetime.date(2024, 1, 1), json.dumps(1))\n").startswith("import datetime\nimport json\n")
    assert add_imports("import sys\ndef csv(): pass\nprint(sys.argv, csv())\n").startswith("import sys")

    assert run_tool("write_file", {"path": "../x", "content": ""}).startswith("ERROR: path '../x' is outside")
    print("harness checks passed")


if __name__ == "__main__":
    main()

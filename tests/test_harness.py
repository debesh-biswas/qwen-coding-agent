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
from hw1_agent import debugging  # noqa: E402
from hw1_agent import state as S  # noqa: E402
from hw1_agent.guards import finish_check  # noqa: E402
from hw1_agent.programming import add_imports, exit_ok, best_version, order_bugs, reordered, failures, fix_keyword_attrs, real_data_tests, gather, parse_tests, passes, pick_best, restore_good  # noqa: E402
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
    with open("test_data.csv", "w") as fh:
        fh.write("x\n1\n")
    assert cmd("python3 p.py test_data.csv", "5").startswith("DIFFERENT")  # named like the real file, but isn't it
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

    # The last edits were never run and crash: the tested version is handed in instead.
    S.VERSIONS["r.py"], S.SPEC_TESTS[:] = ["print(1)\n"], [("python3 r.py", "1", 0)]
    with open("r.py", "w") as fh:
        fh.write("print(x)\n")
    best_version("r.py")
    assert open("r.py").read() == "print(1)\n"
    S.SPEC_TESTS[:] = []

    assert pick_best("p.py", [bad, good, good], [("python3 p.py data.csv", None, 0)])[0] == good
    zero = "print(0)\n"  # parses nothing of the real data: loses even to a crash-free agreeing pair
    assert pick_best("p.py", [zero, zero, good], [("python3 p.py data.csv", None, 0)])[0] == good
    assert "rejects EVERY line" in failures([("python3 p.py data.csv", None, 0)], [(0, "0\n", "")], [])
    # Unknown stdout still has a known exit status; agreeing usage errors are not evidence.
    probe = ("python3 p.py data.csv", None, 0)
    assert exit_ok(probe, (0, "2\n", ""))
    assert not exit_ok(probe, (2, "", "usage: invalid"))
    assert not exit_ok(probe, (0, "", "Traceback (most recent call last)"))
    assert not exit_ok(probe, (None, "", ""))
    assert not passes(probe, (0, "2\n", "")), "unknown stdout is not an exact-output pass"
    usage_out = "print('usage 123')\nraise SystemExit(2)\n"
    assert pick_best("p.py", [usage_out, usage_out, good], [probe])[0] == good
    for result in [(2, "", "usage: invalid"), (None, "", ""),
                   (1, "", "Traceback (most recent call last)\nValueError: invalid")]:
        msg = failures([probe], [result], [])
        assert "expected exit 0" in msg and "python3 p.py data.csv" in msg, msg
    assert "expected exit 0" not in failures([probe], [(0, "2\n", "")], [])
    S.VERSIONS["r.py"], S.SPEC_TESTS[:] = ["print(2)\n"], [("python3 r.py", None, 0)]
    with open("r.py", "w") as fh:
        fh.write(usage_out)
    best_version("r.py")
    assert open("r.py").read() == "print(2)\n", "unknown-output exit regression restored"
    S.SPEC_TESTS[:] = []

    # Options may appear anywhere: a parser where a flag swallows the next argument loses to one that doesn't.
    S.PROMPT += "Options may appear anywhere after the command name.\n"
    otests = [("printf 'x\\n' > t1.csv\npython3 p.py total --json --cat a --file t1.csv", "1", 0), ("python3 p.py top 2 --json", "1", 0)]
    S.REORDERED[:] = reordered(otests)
    assert [c.split("\n")[-1] for _, c in S.REORDERED] == ["python3 p.py total --file t1.csv --cat a --json",
                                                           "python3 p.py top --json 2"], S.REORDERED
    swallow = "import sys\na = sys.argv[2:] + ['none']\nprint(a[a.index('--json') + 1])\n"  # --json eats the next arg
    sane = "import sys\nprint(1)\n"
    assert pick_best("p.py", [swallow, swallow, sane], otests)[0] == sane
    assert "print different results" in failures(otests, pick_best("p.py", [swallow], otests)[1], [])
    S.REORDERED[:] = []
    lib = "def f():\n    return 41 + 1\n"
    assert pick_best("p.py", ["def f():\n    return 0\n", lib, lib], [('python3 -c "from p import f; print(f())"', None, 0)])[0] == lib

    # Tests written from the spec: a tiny file, expected stdout, exit code; the candidate passing them wins even
    # when the others agree with each other.
    tests = parse_tests("```text\n$ printf 'x\\n1\\n2\\n3\\n' > t1.csv\n$ python3 p.py t1.csv\n3\n"
                        "$ python3 p.py nope.csv\n[exit 1]\n$ python3 p.py data.csv\n?\n```", "p.py")
    assert tests == [("printf 'x\\n1\\n2\\n3\\n' > t1.csv\npython3 p.py t1.csv", "3", 0),
                     ("printf 'x\\n1\\n2\\n3\\n' > t1.csv\npython3 p.py nope.csv", "", 1),
                     ("printf 'x\\n1\\n2\\n3\\n' > t1.csv\npython3 p.py data.csv", None, 0)], tests
    assert real_data_tests(tests[:2]) == [("python3 p.py data.csv", None, 0)], real_data_tests(tests[:2])
    assert real_data_tests(tests) == []  # already run on the real file
    assert real_data_tests(tests[:2] + [("python3 p.py other.csv", None, 0)])  # an unchecked call elsewhere doesn't block it
    off = "import sys\nprint(sum(1 for _ in open(sys.argv[1])))\n"  # counts the header too
    code, res, outs = pick_best("p.py", [off, off, good], tests)
    assert code == good and passes(tests[0], res[0]) and not passes(tests[1], res[1]), res  # a crash is no exit 1
    # Feedback on the wrong count: another candidate got it right, so the expectation is credible.
    msg = failures(tests, outs[0], outs)
    assert "2 tests with expected output on it; 2 fail" in msg and "$ printf 'x" in msg and "got: '4', exit 0" in msg, msg
    # Every candidate gets the same "wrong" output: likely a slip in the test, so only the crash is reported.
    msg = failures(tests, outs[0], outs[:2])
    assert "; 1 fail" in msg and "got: '4'" not in msg, msg

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

    # The same edit twice is refused even though the first one changed the file (its new text contains its old text,
    # so it would match again and insert the line a second time).
    grow = {"name": "edit_file", "arguments": json.dumps({"path": "t.csv", "old": "x", "new": "x\ny"})}
    script = iter([[grow], [grow], [done]])
    msgs = [{"role": "system", "content": ""}, {"role": "user", "content": S.PROMPT}]
    try:
        agent_loop.loop(msgs, "", 10)
    except Finish:
        pass
    assert open("t.csv").read().count("y") == 1, open("t.csv").read()

    # Parallel requests: once the first and two others are back, a straggler is not waited for beyond the grace time.
    t0 = time.monotonic()
    assert gather(lambda a: time.sleep(a) or "r%s" % a, [0, 0.1, 0.1, 30], grace=0.5) == ["r0", "r0.1", "r0.1", ""]
    assert time.monotonic() - t0 < 5
    # Queued: at most 2 run at once, in order.
    running, peak = [0], [0]

    def slow(a):
        running[0] += 1
        peak[0] = max(peak[0], running[0])
        time.sleep(0.1)
        running[0] -= 1
        return a
    assert gather(slow, list("abcde"), workers=2) == list("abcde") and peak[0] == 2, peak

    # Missing stdlib imports are added; names the program defines itself are left alone.
    assert add_imports("print(datetime.strptime('2024', '%Y').year)\n").startswith("from datetime import datetime\n")
    assert add_imports("print(datetime.date(2024, 1, 1), json.dumps(1))\n").startswith("import datetime\nimport json\n")
    assert add_imports("import sys\ndef csv(): pass\nprint(sys.argv, csv())\n").startswith("import sys")

    # Investigations disagree on an ID: the model sees each value's log lines; the reports agreeing with its pick win.
    with open("app.log", "w") as fh:
        fh.write("tk-1 granted\ntk-2 still pending\n")
    S.EXISTING.add(os.path.realpath("app.log"))
    debugging.chat = lambda msg, **kw: {"content": "tk-2 was pending.\nANSWER: 2" if "app.log:2: tk-2 still" in msg[0]["content"] else ""}
    a, b = {"id": "tk-1", "w": "x"}, {"id": "tk-2", "w": "y"}
    assert debugging.adjudicate([a, b, a], {}) == [b]

    # A key left as a task ID where a class is asked: repaired from the log lines of that ID, only if verbatim there.
    with open("app.log", "a") as fh:
        fh.write("tk-2 SlotMover START\n")
    S.PROMPT = "1. `id` — the ticket.\n2. `comp` — the component (a class name).\n3. `x` — other.\n"
    debugging.chat = lambda msg, **kw: {"content": '{"comp": "SlotMover"}'}
    assert debugging.repair({"id": "tk-2", "comp": "tk-2", "x": "y"}, debugging.key_specs())["comp"] == "SlotMover"
    debugging.chat = lambda msg, **kw: {"content": '{"comp": "Invented"}'}
    assert debugging.repair({"id": "tk-2", "comp": "tk-2", "x": "y"}, debugging.key_specs())["comp"] == "tk-2"

    # argparse dest of --from is a keyword: args.from doesn't compile; the harness rewrites it.
    assert fix_keyword_attrs("if not args.from or args.to:\n    pass\n") == 'if not getattr(args, "from") or args.to:\n    pass\n'
    assert run_tool("write_file", {"path": "../x", "content": ""}).startswith("ERROR: path '../x' is outside")
    print("harness checks passed")


if __name__ == "__main__":
    main()

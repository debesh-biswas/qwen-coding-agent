"""Run the agent against every case in tests/cases/ and check results.

Each case folder: prompt.txt (single turn) or "turns" in expect.json, optional setup/ (initial
workspace), expect.json with keys:
  status                "success" | "error" (default "success"; exit code must match)
  files                 {path: exact_content}   files that must exist with this content
  absent                [path, ...]             files that must not exist
  message_contains      [fact | [alt, alt], ...] case-insensitive, whole-word match
  message_not_contains  [text, ...]
  summary               {path, max_words, must_contain, must_not_contain,
                         lines (exact non-empty line count), max_lines, line_regex}
  turns                 [{prompt, session: label, status: success|error|any, message_contains,
                          message_not_contains}, ...]  multi-turn; each turn is its own process, labels map
                          to fresh random session IDs; top-level checks run after the last turn
  no_session            true                    single-prompt case run without a session ID
                        (otherwise a fresh random session ID is passed as $2)
  env                   {VAR: value}            override environment for this case
  slow                  true                    only run with --slow
Every setup file not named in files/absent must be unchanged, every setup directory must
still exist, no other new files may appear, and nothing may be written outside the workspace.

Usage: python3 tests/run_tests.py [-k substring] [--repeat N] [--slow]
HW1_SESSION_DIR is set to a fresh temp dir outside the workspace for every case-run.
Needs OPENAI_BASE_URL, MODEL, QWEN_API_KEY in the environment.
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(os.path.dirname(HERE), "run_agent.sh")


def snapshot(root):
    files, dirs = {}, set()
    for d, subdirs, names in os.walk(root):
        dirs.update(os.path.relpath(os.path.join(d, s), root) for s in subdirs)
        for f in names:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                files[os.path.relpath(p, root)] = fh.read()
    return files, dirs


def contains(text, alt):
    """Case-insensitive match that won't let 'TA' match inside 'data' or '12' inside '120'."""
    pat = re.escape(alt)
    if alt[:1].isalpha():
        pat = r"(?<![A-Za-z])" + pat
    elif alt[:1].isdigit():
        pat = r"(?<![\d.])" + pat
    if alt[-1:].isalpha():
        pat += r"(?![A-Za-z])"
    elif alt[-1:].isdigit():
        pat += r"(?![\d])"
    return re.search(pat, text, re.I) is not None


def has(text, fact):
    return any(contains(text, a) for a in (fact if isinstance(fact, list) else [fact]))


def check_turn(t, proc):
    """Per-turn checks; returns (errors, parsed output or None)."""
    errs = []
    want_status = t.get("status", "success")
    try:
        out = json.loads(proc.stdout)
        assert isinstance(out, dict) and set(out) == {"status", "message"}
        assert all(isinstance(v, str) for v in out.values())
    except Exception:
        return ["stdout is not exactly one {status,message} JSON object: %r" % proc.stdout[:300]], None
    if want_status != "any":
        if out["status"] != want_status:
            errs.append("status %s, expected %s (%s)" % (out["status"], want_status, out["message"][:150]))
        if (proc.returncode == 0) != (want_status == "success"):
            errs.append("exit code %d" % proc.returncode)
    elif (proc.returncode == 0) != (out["status"] == "success"):
        errs.append("exit code %d inconsistent with status %s" % (proc.returncode, out["status"]))
    for fact in t.get("message_contains", []):
        if not has(out["message"], fact):
            errs.append("message missing %r: %r" % (fact, out["message"][:200]))
    for bad in t.get("message_not_contains", []):
        if has(out["message"], bad):
            errs.append("message should not contain %r: %r" % (bad, out["message"][:200]))
    return errs, out


def check(case_dir, exp, work):
    errs = []

    setup = os.path.join(case_dir, "setup")
    before, before_dirs = snapshot(setup) if os.path.isdir(setup) else ({}, set())
    after, after_dirs = snapshot(work)
    files = exp.get("files", {})
    absent = exp.get("absent", [])
    summary = exp.get("summary")
    for path, content in files.items():
        got = after.get(path)
        if got is None:
            errs.append("missing %s" % path)
        elif got.decode("utf-8", "replace") != content:
            note = " (differs only by trailing newline)" if got.decode().rstrip("\n") == content.rstrip("\n") else ""
            errs.append("%s content %r != %r%s" % (path, got[:120], content[:120], note))
    for path in absent:
        if path in after:
            errs.append("%s should have been deleted" % path)
    expected_new = set(files) | ({summary["path"]} if summary else set())
    for path, data in before.items():
        if path not in files and path not in absent and after.get(path) != data:
            errs.append("unrelated file %s was changed/removed" % path)
    for path in set(after) - set(before) - expected_new:
        errs.append("unexpected new file %s" % path)
    for d in sorted(before_dirs - after_dirs):
        errs.append("directory %s was removed" % d)

    if summary:
        got = after.get(summary["path"])
        if got is None:
            return errs + ["summary %s missing" % summary["path"]]
        text = got.decode("utf-8", "replace")
        lines = [l for l in text.splitlines() if l.strip()]
        if "max_words" in summary and len(text.split()) > summary["max_words"]:
            errs.append("summary has %d words > %d" % (len(text.split()), summary["max_words"]))
        if "lines" in summary and len(lines) != summary["lines"]:
            errs.append("summary has %d lines, expected %d" % (len(lines), summary["lines"]))
        if "max_lines" in summary and len(lines) > summary["max_lines"]:
            errs.append("summary has %d lines > %d" % (len(lines), summary["max_lines"]))
        if "line_regex" in summary:
            errs += ["summary line doesn't match %r: %r" % (summary["line_regex"], l)
                     for l in lines if not re.match(summary["line_regex"], l)]
        for fact in summary.get("must_contain", []):
            if not has(text, fact):
                errs.append("summary missing %r" % (fact,))
        for bad in summary.get("must_not_contain", []):
            if has(text, bad):
                errs.append("summary contains unsupported %r" % bad)
        if errs:
            errs.append("summary text: %r" % text[:400])
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--slow", action="store_true", help="also run cases marked slow")
    a = ap.parse_args()
    failed = total = 0
    for case in sorted(c for c in os.listdir(os.path.join(HERE, "cases")) if a.k in c):
        case_dir = os.path.join(HERE, "cases", case)
        exp = json.load(open(os.path.join(case_dir, "expect.json"), encoding="utf-8"))
        if exp.get("slow") and not a.slow:
            continue
        if "turns" in exp:
            turns = exp["turns"]
        else:
            prompt = open(os.path.join(case_dir, "prompt.txt"), encoding="utf-8").read().strip()
            turns = [dict(exp, prompt=prompt, session=None if exp.get("no_session") else "S")]
        env = dict(os.environ, **exp.get("env", {}))
        for i in range(a.repeat):
            base = tempfile.mkdtemp()
            work = os.path.join(base, "task ws")  # space in path; parent lets us detect ../ escapes
            if os.path.isdir(os.path.join(case_dir, "setup")):
                shutil.copytree(os.path.join(case_dir, "setup"), work)
            else:
                os.makedirs(work)
            t = time.time()
            sess_dir = tempfile.mkdtemp()  # outside base, so the escape check still works
            run_env = dict(env, HW1_SESSION_DIR=sess_dir)
            ids, errs, proc = {}, [], None
            for n, turn in enumerate(turns, 1):
                label = turn.get("session")
                cmd = ["bash", AGENT, turn["prompt"]]
                if label is not None:
                    cmd.append(ids.setdefault(label, str(random.randint(10**8, 10**9))))
                try:
                    proc = subprocess.run(cmd, cwd=work, env=run_env, capture_output=True, text=True, timeout=180)
                    te, _ = check_turn(turn, proc)
                except subprocess.TimeoutExpired:
                    te, proc = ["timeout (>180 s)"], None
                pre = "turn %d: " % n if "turns" in exp else ""
                errs += [pre + e for e in te]
                if te and proc is not None and os.environ.get("VERBOSE"):
                    print(proc.stderr[-3000:])
            errs += check(case_dir, exp, work)
            shutil.rmtree(sess_dir, ignore_errors=True)
            escaped = [f for f in os.listdir(base) if f != "task ws"]
            if escaped:
                errs.append("files written outside workspace: %s" % escaped)
            total += 1
            failed += bool(errs)
            print("%s %-32s run %d  %5.1fs" % ("FAIL" if errs else "PASS", case, i + 1, time.time() - t), flush=True)
            for e in errs:
                print("     - " + e)
            shutil.rmtree(base, ignore_errors=True)
    print("\n%d/%d passed" % (total - failed, total))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

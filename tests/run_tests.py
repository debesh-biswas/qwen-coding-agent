"""Run the agent against every case in tests/cases/ and check results.

Each case folder: prompt.txt, optional setup/ (initial workspace), expect.json with keys:
  status            "success" | "error" (default "success"; exit code must match)
  files             {path: exact_content}   files that must exist with this content
  absent            [path, ...]             files that must not exist
  message_contains  [fact | [alt, alt], ...] case-insensitive
  summary           {path, max_words, must_contain: [fact | [alt, ...]]}
Every setup file not named in files/absent must be unchanged, and no other new files may appear.

Usage: python3 tests/run_tests.py [-k substring] [--repeat N]
Needs OPENAI_BASE_URL, MODEL, QWEN_API_KEY in the environment.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = os.path.join(os.path.dirname(HERE), "run_agent.sh")


def snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = fh.read()
    return out


def has(text, fact):
    alts = fact if isinstance(fact, list) else [fact]
    return any(a.lower() in text.lower() for a in alts)


def check(case_dir, work, proc):
    exp = json.load(open(os.path.join(case_dir, "expect.json")))
    errs = []
    want_status = exp.get("status", "success")
    try:
        out = json.loads(proc.stdout)
        assert isinstance(out, dict) and set(out) == {"status", "message"}
        assert all(isinstance(v, str) for v in out.values())
    except Exception:
        return ["stdout is not exactly one {status,message} JSON object: %r" % proc.stdout[:300]]
    if out["status"] != want_status:
        errs.append("status %s, expected %s (%s)" % (out["status"], want_status, out["message"][:150]))
    if proc.returncode != (0 if want_status == "success" else 1) and not (want_status == "error" and proc.returncode):
        errs.append("exit code %d" % proc.returncode)

    before = snapshot(os.path.join(case_dir, "setup")) if os.path.isdir(os.path.join(case_dir, "setup")) else {}
    after = snapshot(work)
    files = exp.get("files", {})
    summary = exp.get("summary")
    for path, content in files.items():
        got = after.get(path)
        if got is None:
            errs.append("missing %s" % path)
        elif got.decode("utf-8", "replace") != content:
            note = " (differs only by trailing newline)" if got.decode().rstrip("\n") == content.rstrip("\n") else ""
            errs.append("%s content %r != %r%s" % (path, got[:120], content[:120], note))
    for path in exp.get("absent", []):
        if path in after:
            errs.append("%s should have been deleted" % path)
    expected_new = set(files) | ({summary["path"]} if summary else set())
    for path, data in before.items():
        if path not in files and path not in exp.get("absent", []) and after.get(path) != data:
            errs.append("unrelated file %s was changed/removed" % path)
    for path in set(after) - set(before) - expected_new:
        errs.append("unexpected new file %s" % path)

    for fact in exp.get("message_contains", []):
        if not has(out["message"], fact):
            errs.append("message missing %r: %r" % (fact, out["message"][:200]))
    if summary:
        got = after.get(summary["path"])
        if got is None:
            errs.append("summary %s missing" % summary["path"])
        else:
            text = got.decode("utf-8", "replace")
            if "max_words" in summary and len(text.split()) > summary["max_words"]:
                errs.append("summary has %d words > %d" % (len(text.split()), summary["max_words"]))
            for fact in summary.get("must_contain", []):
                if not has(text, fact):
                    errs.append("summary missing %r" % (fact,))
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    cases = sorted(c for c in os.listdir(os.path.join(HERE, "cases")) if a.k in c)
    failed = 0
    for case in cases:
        case_dir = os.path.join(HERE, "cases", case)
        prompt = open(os.path.join(case_dir, "prompt.txt"), encoding="utf-8").read().strip()
        for i in range(a.repeat):
            base = tempfile.mkdtemp()
            work = os.path.join(base, "ws")  # parent dir lets us detect ../ escapes
            if os.path.isdir(os.path.join(case_dir, "setup")):
                shutil.copytree(os.path.join(case_dir, "setup"), work)
            else:
                os.makedirs(work)
            t = time.time()
            try:
                proc = subprocess.run(["bash", AGENT, prompt], cwd=work, capture_output=True,
                                      text=True, timeout=180)
                errs = check(case_dir, work, proc)
            except subprocess.TimeoutExpired:
                errs, proc = ["timeout (>180 s)"], None
            escaped = [f for f in os.listdir(base) if f != "ws"]
            if escaped:
                errs.append("files written outside workspace: %s" % escaped)
            status = "PASS" if not errs else "FAIL"
            failed += bool(errs)
            print("%s %-32s run %d  %5.1fs" % (status, case, i + 1, time.time() - t))
            for e in errs:
                print("     - " + e)
            if errs and proc is not None and os.environ.get("VERBOSE"):
                print(proc.stderr[-3000:])
            shutil.rmtree(base, ignore_errors=True)
    total = len(cases) * a.repeat
    print("\n%d/%d passed" % (total - failed, total))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

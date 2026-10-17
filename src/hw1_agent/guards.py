"""Code-mode guards: which files may change, guessed values, and checks before a success finish."""
import json
import os
import re

from . import state as S
from .util import safe_path


DELETE_RE = re.compile(r"\b(delete|remove|erase|get rid of|trash|discard|clean up)\b", re.I)


def blocked(p):
    """Only modify files the user actually named: blocks instructions injected via file contents."""
    if S.CODE_MODE and os.path.realpath(os.path.join(S.WORKSPACE, p)) not in S.EXISTING:
        return None  # new scratch/output files are fine
    if os.path.basename(p).lower() not in S.GUARD.lower():
        return "ERROR: %s is not named in the user's request; only files the user asked for may be changed" % p


def unseen(text):
    """ID-like tokens (with a digit, or CamelCase class names) that never appeared in the prompt or any tool output."""
    seen = S.PROMPT + "\n".join(S.SEEN)
    toks = re.findall(r"[A-Za-z0-9_][\w.:-]*\d[\w.:-]*|\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b", str(text))
    return sorted({t.rstrip(".:-") for t in toks if t.rstrip(".:-") not in seen and len(t) >= 4})


def finish_check():
    """Code mode: return a complaint (at most 2 per kind) if the deliverables look wrong, else None."""
    def bounce(kind, msg):
        S.BOUNCES[kind] = S.BOUNCES.get(kind, 0) + 1
        return msg if S.BOUNCES[kind] <= 2 else None
    files = sorted(S.WRITTEN | {d for d in S.DELIV if os.path.isfile(os.path.join(S.WORKSPACE, d))})
    for f in files:
        if f.endswith(".py") and os.path.isfile(safe_path(f)):
            try:
                with open(safe_path(f), encoding="utf-8", errors="replace") as fh:
                    compile(fh.read(), f, "exec")
            except (SyntaxError, ValueError) as e:
                m = bounce("py", "ERROR: %s does not compile: %s. Fix it before finishing." % (f, e))
                if m:
                    return m
    keys = re.findall(r"^\s*\d+\.\s+`(\w+)`", S.PROMPT, re.M)
    reqs = re.findall(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*", S.PROMPT, re.M)
    data = {}  # parsed .json deliverables
    for f in files:
        if f.endswith(".json") and os.path.isfile(safe_path(f)):
            try:
                with open(safe_path(f), encoding="utf-8") as fh:
                    d = json.load(fh)
                if not isinstance(d, dict):
                    raise ValueError("top level must be a JSON object")
                data[f] = d
                miss = [k for k in keys if k not in d] if os.path.basename(f) in S.PROMPT else []
                if miss:
                    raise ValueError("missing keys: " + ", ".join(miss))
            except ValueError as e:
                m = bounce("json", "ERROR: %s is not valid: %s. Fix it before finishing." % (f, e))
                if m:
                    return m
    if S.UNTESTED:
        m = bounce("untested", "ERROR: you changed %s but have not run it since. Before finishing, test it with run_command: %s "
                   "Compare each output with the spec character by character, fix bugs with edit_file, re-run, then finish." % (
                       ", ".join(sorted(S.UNTESTED)), ("run one or more commands for EACH requirement: " + "; ".join(
                           "%s %s" % r for r in reqs) + ".") if reqs else "run it on the real data and on edge cases."))
        if m:
            return m
    if S.OPEN_FAILS:
        m = bounce("failing", "ERROR: %d of your test runs since the last change to the program came back DIFFERENT or "
                   "CRASH (%s). Fix the program (or, if your expectation was wrong per the spec, re-run that command with "
                   "the corrected expect) before finishing." % (len(S.OPEN_FAILS), "; ".join(S.OPEN_FAILS[:4])))
        if m:
            return m
    py = sorted(d for d in S.DELIV if d.endswith(".py"))
    if py and not S.BOUNCES.get("review"):
        S.BOUNCES["review"] = 1
        return ("FINAL REVIEW before finishing %s: go through the task's requirements one at a time%s. For each, run a "
                "command that proves it on a tiny test file you create (include tricky rows the spec mentions) and compare "
                "the output with the spec exactly (format, rounding, sorting, exit code, stderr, valid JSON). Fix any "
                "mismatch with edit_file, then finish." % (", ".join(py), (" (" + "; ".join("%s %s" % r for r in reqs) + ")") if reqs else ""))
    for f, d in data.items():
        empty = [k for k, v in d.items() if v is None or str(v).strip().lower() in ("", "none", "null", "unknown", "n/a")]
        guessed = unseen(" ".join(str(v) for v in d.values() if isinstance(v, str)))
        if guessed:
            m = bounce("guess", "ERROR: these values in %s never appeared in any tool output, so they are guesses: %s. "
                       "Find each one in the files (search the previous ID in the chain) and fix %s." % (f, ", ".join(guessed), f))
            if m:
                return m
        if empty:
            m = bounce("empty", "ERROR: %s has no real value for: %s. Search the files for them (try other spellings of "
                       "the ID, e.g. order_ref=, id=, the order number alone), then rewrite %s." % (f, ", ".join(empty), f))
            if m:
                return m
    gone = sorted(d for d in S.DELIV if not os.path.isfile(os.path.join(S.WORKSPACE, d)))
    if gone:
        return bounce("files", "ERROR: required file(s) not written yet: %s. Write them before finishing." % ", ".join(gone))


def deliverables_exist():
    got = sorted(d for d in S.DELIV if os.path.isfile(os.path.join(S.WORKSPACE, d)))
    return got if S.DELIV and len(got) == len(S.DELIV) else []

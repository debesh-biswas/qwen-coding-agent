"""Programming tasks: plan, generate candidate programs, pick the best, and keep the best tested version."""
import ast
import os
import re
import shutil
import subprocess
import tempfile
import time

from . import prompts
from . import state as S
from .llm import THINK_RE, chat
from .tools.files import syntax_note
from .tools.shell import matches
from .util import log, safe_env, safe_path


CODE_BLOCK_RE = re.compile(r"```[ \t]*(?:python3?|py)?[ \t]*\n(.*?)```", re.S | re.I)


def ensure_main_call(code):
    """Small models often define main() and forget to call it (the program then prints nothing)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    called = any(isinstance(n, (ast.If, ast.Expr)) for n in tree.body if not isinstance(n, ast.Expr) or isinstance(n.value, ast.Call))
    if "main" in defs and not called:
        log("added missing main() call")
        return code.rstrip("\n") + '\n\n\nif __name__ == "__main__":\n    main()\n'
    return code


def gen_code(messages, temp):
    old, S.TEMP = S.TEMP, temp
    try:
        return THINK_RE.sub("", chat(messages, tools=False).get("content") or "")
    finally:
        S.TEMP = old


def run_versions(base, codes, cmds, timeout, stop):
    """Run each version of a program (saved as base in its own temp dir) on the same commands. Returns per version a
    list of (exit code, stdout, stderr), exit code None on timeout; commands after monotonic time `stop` are skipped."""
    tmp = tempfile.mkdtemp(prefix="hw1-ver-")
    try:
        outs = []
        for i, code in enumerate(codes):
            f = os.path.join(tmp, str(i), base)
            os.makedirs(os.path.dirname(f))
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(code)
            res = []
            for c in cmds:
                if time.monotonic() > stop:
                    break
                try:
                    p = subprocess.run(c.replace(base, '"%s"' % f), shell=True, cwd=S.WORKSPACE, capture_output=True,
                                       text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, env=safe_env())
                    res.append((p.returncode, p.stdout, p.stderr))
                except subprocess.TimeoutExpired:
                    res.append((None, "", ""))
            outs.append(res)
        return outs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pick_best(base, codes, cmds):
    """Run every candidate program on the same commands; keep the one whose outputs agree most with the others
    (independent samples rarely share the same bug), penalising crashes and syntax errors."""
    outs = run_versions(base, codes, cmds, 20, S.DEADLINE - 60)
    crashed = lambda r: r[0] is None or "Traceback (most recent call last)" in r[2] or "SyntaxError" in r[2]
    scores = []
    for i, res in enumerate(outs):
        sc = 0
        for k, r in enumerate(res):
            sc -= 3 if crashed(r) else 0
            if r[1].strip() not in ("", "0", "0.00"):  # agreeing on "nothing parsed" is no evidence
                sc += sum(1 for j, o in enumerate(outs) if j != i and k < len(o) and o[k][:2] == r[:2] and not crashed(r))
        scores.append(sc)
    log("CANDIDATE SCORES:", scores)
    return codes[max(range(len(codes)), key=lambda i: (scores[i], -i))]


def write_program(messages, path):
    """Programming tasks: plan in plain text, then the whole program as one code block (no JSON escaping), saved
    by the harness; the normal tool loop then tests and fixes it."""
    messages.append({"role": "user", "content": prompts.PLAN_STEP})
    plan = THINK_RE.sub("", chat(messages, tools=False).get("content") or "").strip()
    log("PLAN:", plan[:1500])
    messages.append({"role": "assistant", "content": plan})
    messages.append({"role": "user", "content": prompts.TESTS_STEP % path})
    cmds_reply = THINK_RE.sub("", chat(messages, tools=False).get("content") or "")
    messages.pop()
    base = os.path.basename(path)
    cmds = [c.strip() for b in re.findall(r"```[a-z]*\n(.*?)```", cmds_reply, re.S) for c in b.split("\n")
            if base in c and not c.strip().startswith("#")][:12]
    log("TEST COMMANDS:", cmds)
    messages.append({"role": "user", "content": prompts.CODE_STEP % (path, prompts.PROG_TIPS)})
    codes, reply = [], ""
    for temp in (0, 0.7, 0.7):  # several candidates; the harness keeps the one the others agree with most
        if codes and (not cmds or S.DEADLINE - time.monotonic() < 0.6 * (S.DEADLINE - S.START)):
            break
        reply = gen_code(messages, temp)
        blocks = CODE_BLOCK_RE.findall(reply)
        if blocks and max(blocks, key=len).strip():
            codes.append(ensure_main_call(max(blocks, key=len)))
    code = pick_best(base, codes, cmds) if len(codes) > 1 else (codes[0] if codes else "")
    log("PROGRAM: %d lines (from %d candidates)" % (code.count("\n") + 1, len(codes)))
    if not code.strip():
        messages.append({"role": "assistant", "content": reply.strip()})
        messages.append({"role": "user", "content": "Use write_file to create %s, then test it." % path})
        return
    full = safe_path(path)
    with open(full, "w", encoding="utf-8", newline="") as fh:
        fh.write(code)
    S.WRITTEN.add(path)
    S.UNTESTED.add(os.path.basename(path))
    messages.append({"role": "assistant", "content": "```python\n%s```" % code})
    messages.append({"role": "user", "content": prompts.REVIEW_STEP})
    review = THINK_RE.sub("", chat(messages, tools=False).get("content") or "").strip()
    log("REVIEW:", review[-1500:])
    messages.append({"role": "assistant", "content": review})
    messages.append({"role": "user", "content": prompts.FIX_STEP % (path, code.count("\n") + 1, syntax_note(path))})
    S.ACTIONS.append("write_program(%s)" % path)


def best_version(d):
    """Re-run the model's own tests on every version of the program it ran; keep the one passing most (ties: newest).
    Late edits often break things that worked."""
    vers, base = S.VERSIONS.get(d, [])[-6:], os.path.basename(d)
    tests = list(S.TESTS.items())[-30:]
    if len(vers) < 2 or not tests:
        return
    outs = run_versions(base, vers, [c for c, _ in tests], 10, min(time.monotonic() + 40, S.DEADLINE - 5))
    if any(len(o) < len(tests) for o in outs):
        log("version check out of time")
        return
    scores = [sum(r[0] is not None and matches(exp, r[1], r[2]) for (_, exp), r in zip(tests, o)) for o in outs]
    best = max(range(len(vers)), key=lambda i: (scores[i], i))
    log("VERSION SCORES:", scores)
    with open(safe_path(d), encoding="utf-8", errors="replace") as fh:
        cur = fh.read()
    # an untested final edit only loses to a version that passed something
    cur_score = scores[vers.index(cur)] if cur in vers else 0
    if vers[best] != cur and scores[best] > cur_score:
        with open(safe_path(d), "w", encoding="utf-8", newline="") as fh:
            fh.write(vers[best])
        log("kept version %d of %s (passes %d of its own tests)" % (best, d, scores[best]))


def restore_good():
    """Code mode: never hand in a program that is worse than one we already had (edits often break working code)."""
    for d, vers in S.VERSIONS.items():
        good = [v for v in vers if v not in S.CRASHED]
        try:
            with open(safe_path(d), encoding="utf-8", errors="replace") as fh:
                cur = fh.read()
            if good and cur != good[-1] and (syntax_note(d) or cur in S.CRASHED):
                with open(safe_path(d), "w", encoding="utf-8", newline="") as fh:
                    fh.write(good[-1])
                log("restored last working version of", d)
        except OSError as e:
            log("restore failed:", e)
        best_version(d)

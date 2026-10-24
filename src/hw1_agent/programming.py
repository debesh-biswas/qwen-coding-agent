"""Programming tasks: plan, generate candidate programs, pick the best, and keep the best tested version."""
import ast
import builtins
import keyword
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from . import prompts
from . import state as S
from .llm import THINK_RE, chat
from .tools.files import syntax_note
from .tools.shell import matches, real_data, same_format
from .util import log, safe_env, safe_path


CODE_BLOCK_RE = re.compile(r"```[ \t]*(?:python3?|py)?[ \t]*\n(.*?)```", re.S | re.I)
STDLIB = getattr(sys, "stdlib_module_names", {"argparse", "collections", "csv", "datetime", "decimal", "json", "math",
                                               "os", "re", "statistics", "sys", "time"})
DT_CLASS = {"strptime", "now", "utcnow", "fromisoformat", "fromtimestamp", "combine"}  # only exist on datetime.datetime


def fix_keyword_attrs(code):
    """args.from (argparse dest of --from) is a SyntaxError the model cannot see; getattr(args, "from") is what it means.
    Applied only when the program does not compile and the rewrite does."""
    if compiles(code):
        return code
    fixed = re.sub(r"\b([A-Za-z_]\w*)\.(%s)\b(?!\s*\()" % "|".join(keyword.kwlist), r'getattr(\1, "\2")', code)
    if fixed != code and compiles(fixed):
        log("rewrote keyword attributes (e.g. args.from)")
        return fixed
    return code


def add_imports(code):
    """Small models often use a stdlib module they never imported, and every candidate dies with NameError."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    nodes, bound = list(ast.walk(tree)), set(dir(builtins))
    for n in nodes:
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            bound |= {(a.asname or a.name).split(".")[0] for a in n.names}
        elif isinstance(n, (ast.FunctionDef, ast.ClassDef, ast.ExceptHandler)):
            bound.add(n.name)
        elif isinstance(n, ast.arg) or isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Load):
            bound.add(n.arg if isinstance(n, ast.arg) else n.id)
    missing = sorted({n.id for n in nodes if isinstance(n, ast.Name) and n.id in STDLIB and n.id not in bound})
    if not missing:
        return code
    attrs = {n.attr for n in nodes if isinstance(n, ast.Attribute) and getattr(n.value, "id", "") == "datetime"}
    lines = ["from datetime import datetime" if m == "datetime" and attrs & DT_CLASS
             and not attrs & {"datetime", "date", "timedelta", "timezone"} else "import " + m for m in missing]
    log("added missing imports:", lines)
    return "\n".join(lines) + "\n" + code


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


def run_versions(base, codes, cmds, timeout, stop):
    """Run each version of a program on the same commands, in a temp dir that links every workspace file except the
    program itself (so both `python3 x.py` and `from x import ...` use that version). Returns per version a list of
    (exit code, stdout, stderr), exit code None on timeout; commands after monotonic time `stop` are skipped."""
    tmp = tempfile.mkdtemp(prefix="hw1-ver-")
    try:
        outs = []
        for i, code in enumerate(codes):
            d = os.path.join(tmp, str(i))
            os.makedirs(d)
            for n in os.listdir(S.WORKSPACE):
                if n not in (base, "__pycache__"):
                    os.symlink(os.path.join(S.WORKSPACE, n), os.path.join(d, n))
            with open(os.path.join(d, base), "w", encoding="utf-8") as fh:
                fh.write(code)
            res = []
            for c in cmds:
                if time.monotonic() > stop:
                    break
                try:
                    p = subprocess.run(c, shell=True, cwd=d, capture_output=True,
                                       text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL, env=safe_env())
                    res.append((p.returncode, p.stdout, p.stderr))
                except subprocess.TimeoutExpired:
                    res.append((None, "", ""))
            outs.append(res)
        return outs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def parse_tests(reply, base):
    """Tests written before any code, as a transcript: "$ cmd" lines; after a command that runs the program, its expected
    stdout ("?" = not worked out) and an optional "[exit N]" line. Other commands (creating input files) are run before
    every later test. Returns [(command, expected stdout or None, exit code)]."""
    steps = []
    for block in re.findall(r"```[a-z]*\n(.*?)```", reply, re.S) or [reply]:
        for line in block.split("\n"):
            if line.startswith("$ "):
                steps.append((line[2:].strip(), []))
            elif steps:
                steps[-1][1].append(line)
    setup, tests = [], []
    for cmd, lines in steps:
        if not re.search(r"\b%s\b" % re.escape(os.path.splitext(base)[0]), cmd):
            setup.append("\n".join([cmd] + lines).rstrip())  # a heredoc body stays with its command
            continue
        out = "\n".join(lines).strip()
        rc = re.search(r"\n?\[exit (\d+)\]$", out)
        out = out[:rc.start()].strip() if rc else out
        if all(cmd != t[0].split("\n")[-1] for t in tests):  # small models repeat themselves
            tests.append(("\n".join(setup + [cmd]), None if out == "?" else out, int(rc.group(1)) if rc else 0))
    return tests[:16]


def crashed(r):
    return r[0] is None or "Traceback (most recent call last)" in r[2] or "SyntaxError" in r[2]


def exit_ok(test, r):
    """Reliable even when the model's expected numbers are not (it adds badly): which calls are valid usage. A crash
    never passes, even a test expecting exit 1."""
    return test[1] is not None and r[0] == test[2] and not crashed(r)


def passes(test, r):
    """Whitespace-insensitive (the model types a TAB as spaces); for an error test only the exit code counts (the
    model often puts the error message, which goes to stderr, under stdout)."""
    return exit_ok(test, r) and (test[2] != 0 or r[1].split() == test[1].split())


def real_data_tests(tests):
    """The model's tests run on tiny files it wrote; the same commands on the provided data file (largest one present
    at start) show a parser that rejects the real format. Up to 3, expected output unknown."""
    data = [f for f in S.EXISTING if os.path.basename(f) != "TASK.md" and os.path.isfile(f)]
    if not data or any(t[1] is None for t in tests):
        return []
    real = os.path.relpath(max(data, key=os.path.getsize), S.WORKSPACE)
    out = []
    for cmd, _, rc in tests:
        *setup, run = cmd.split("\n")
        made = re.findall(r">\s*(\S+)", "\n".join(setup))
        new = next((re.sub(r"(?<!\S)%s(?!\S)" % re.escape(f), real, run) for f in made if f in run.split()), None)
        if rc == 0 and new and new not in [t[0] for t in out]:
            out.append((new, None, 0))
    return out[:3]


def reordered(tests):
    """When the spec lets options appear anywhere: the model's passing-path test calls again with their options moved
    first, in reverse order (a flag like --json then precedes the option that followed it). Same output required, so
    an argument parser that loses an option shows without any expected value. Returns [(test index, command)]."""
    if not re.search(r"\b(anywhere|any order)\b", S.PROMPT, re.I):
        return []
    runs = [t[0].split("\n")[-1] for t in tests]
    toks_all = [s for r in runs for s in [r.split()]]
    flags = {t for ts in toks_all for j, t in enumerate(ts) if t.startswith("--") and (j + 1 == len(ts) or ts[j + 1].startswith("--"))}
    out = []
    for k, (cmd, _, rc) in enumerate(tests):
        *setup, run = cmd.split("\n")
        if rc != 0 or re.search(r"[|;&<>$`]", run):
            continue
        try:
            toks = shlex.split(run)
        except ValueError:
            continue
        i = next((j for j, t in enumerate(toks) if t.endswith(".py")), None)
        if i is None or len(toks) < i + 3 or toks[i + 1].startswith("-"):
            continue
        rest, pos, opts, j = toks[i + 2:], [], [], 0
        while j < len(rest):
            n = 2 if rest[j].startswith("--") and rest[j] not in flags and j + 1 < len(rest) else 1
            (opts if rest[j].startswith("--") else pos).append(rest[j:j + n])
            j += n
        new = toks[:i + 2] + [x for o in reversed(opts) for x in o] + [x for p in pos for x in p]
        if opts and new != toks:
            out.append((k, "\n".join(setup + [" ".join(map(shlex.quote, new))])))
    return out[:6]


def order_bugs(tests, res):
    """[(original call, reordered call, its result, reordered result)] where moving the options changed the result."""
    n = len(tests)
    return [(tests[k][0].split("\n")[-1], c.split("\n")[-1], res[k], res[n + j]) for j, (k, c) in enumerate(S.REORDERED)
            if n + j < len(res) and res[n + j][:2] != res[k][:2]]


def parsed_nothing(test, r):
    """A command on the provided (large) data whose output is empty or all zeros: the parser rejects every line."""
    return test[1] is None and r[0] == 0 and not re.search(r"[1-9]", r[1])


def pick_best(base, codes, tests):
    """Run every candidate program on the tests; keep the one passing most of them (expected outputs worked out from
    the spec), then whose outputs agree most with the others (independent samples rarely share the same bug),
    penalising crashes. Returns (program, its results, every candidate's results)."""
    outs = run_versions(base, codes, [t[0] for t in tests] + [c for _, c in S.REORDERED], 20, S.DEADLINE - 15)
    scores = []
    for i, res in enumerate(outs):
        sc = -4 * len(order_bugs(tests, res))
        for k, r in enumerate(res[:len(tests)]):
            sc += 2 * exit_ok(tests[k], r) + 3 * passes(tests[k], r) - 3 * crashed(r) - 5 * parsed_nothing(tests[k], r)
            if r[1].strip() not in ("", "0", "0.00"):  # agreeing on "nothing parsed" is no evidence
                sc += sum(1 for j, o in enumerate(outs) if j != i and k < len(o) and o[k][:2] == r[:2] and not crashed(r))
        scores.append(sc)
    log("CANDIDATE SCORES:", scores, "tests passed:", [sum(passes(t, r) for t, r in zip(tests, o)) for o in outs],
        "option-order bugs:", [len(order_bugs(tests, o)) for o in outs])
    for i, res in enumerate(outs):
        err = next((r[2].strip().split("\n")[-1] for r in res if crashed(r) and r[2].strip()), None)
        if err:
            log("candidate %d crashed: %s" % (i, err[:200]))
    best = max(range(len(codes)), key=lambda i: (scores[i], -i))
    return codes[best], outs[best], outs


def failures(tests, res, outs):
    """Tell the model which of its own tests the chosen program fails, so its first fix is aimed. Only credible ones:
    a wrong exit code, or an output another (independently written) candidate got right; an output every candidate
    gets "wrong" is usually the model's own arithmetic slip, and chasing it breaks working code."""
    known = [t for t in tests if t[1] is not None]
    zero = next((t[0].split("\n")[-1] for t, r in zip(tests, res) if parsed_nothing(t, r)), None)
    if zero:  # nothing else matters until the parser accepts the real data
        return ("\n\nThe harness ran `%s` on the provided data: it printed nothing or only zeros, so your parser rejects "
                "EVERY line of the real file. Fix that first: read the first 3 lines of the data file, run your line "
                "parsing (regex/split) on each with python3 -c, compare with the format in the spec, and fix it." % zero)
    order = "".join("\n\nOptions may appear in any order, but these two calls print different results, so your argument "
                    "parsing loses or misreads an option (e.g. a flag that takes no value, like --json, must not consume "
                    "the next argument). Fix the parsing loop:\n$ %s\n  got: %r, exit %s\n$ %s\n  got: %r, exit %s" % (
                        a, ra[1].strip()[:200], ra[0], b, rb[1].strip()[:200], rb[0]) for a, b, ra, rb in order_bugs(tests, res)[:2])
    bad = [(t, r) for k, (t, r) in enumerate(zip(tests, res)) if t[1] is not None and not passes(t, r)
           and (not exit_ok(t, r) or any(k < len(o) and passes(t, o[k]) for o in outs))]
    if not bad:
        return order or ("\n\nThe harness ran your %d tests with expected output on it: no clear failures." % len(known)
                         if known else "")
    bad.sort(key=lambda b: exit_ok(*b))  # wrong exit codes first: those expectations are the reliable ones
    text = ["\n\nThe harness ran your %d tests with expected output on it; %d fail. A wrong exit code is a real bug. "
            "For a wrong output, first recompute the expected value by hand from the test file (your test may be "
            "wrong). Then find the branch of the program that handles that exact command and option, quote its lines, "
            "and fix that code with edit_file:" % (len(known), len(bad))]
    for (cmd, out, rc), r in bad[:6]:
        run = cmd.split("\n")[-1]
        files = [s for s in cmd.split("\n")[:-1] if any(f in run for f in re.findall(r">\s*(\S+)", s))]  # its inputs
        err = r[2].strip().split("\n")[-1][:200]
        text.append("%s$ %s\n  expected: %r, exit %d\n  got: %r, exit %s%s" % (
            "".join("$ %s\n" % f for f in files), run, out[:300], rc, r[1].strip()[:300],
            "timeout" if r[0] is None else r[0], ", stderr: " + err if err else ""))
    return order + "\n".join(text)


def gather(gen, asks, grace=15, need=1, workers=3):
    """gen(ask) for every ask, through a queue served in order by at most `workers` threads (the course endpoint answers
    "busy" above ~4 parallel requests); after a busy reply, a worker that finishes its ask stops, so fewer run at once.
    Once the first `need` asks (the tests) and two others are back, wait at most `grace` more seconds: the slowest
    candidate often takes half a minute longer. Daemon threads, so a straggler never holds the process open past the
    time limit; a failed or abandoned ask gives ""."""
    replies, todo, lock, stop = [None] * len(asks), list(range(len(asks))), threading.Lock(), threading.Event()
    live = [min(workers, len(asks))]
    busy0 = S.BUSY

    def work():
        while not stop.is_set():
            with lock:
                if not todo or S.BUSY > busy0 and live[0] > 1:
                    live[0] -= 1
                    return
                i = todo.pop(0)
            try:
                replies[i] = gen(asks[i])
            except Exception as e:  # noqa: BLE001 - includes Finish (timeout): the caller copes with an empty reply
                log("parallel request failed:", e)
                replies[i] = ""
    for _ in range(live[0]):
        threading.Thread(target=work, daemon=True).start()
    enough = None
    while None in replies and time.monotonic() < S.DEADLINE - 5:
        if enough is None and None not in replies[:need] and sum(r is not None for r in replies[need:]) >= 2:
            enough = time.monotonic()
        if enough and time.monotonic() - enough > grace:
            log("not waiting for the slowest candidate")
            break
        time.sleep(0.2)
    stop.set()  # queued asks not started yet are dropped
    return [r or "" for r in replies]


def write_program(messages, path):
    """Programming tasks: plan in plain text, then the whole program as one code block (no JSON escaping), saved
    by the harness; the normal tool loop then tests and fixes it."""
    messages.append({"role": "user", "content": prompts.PLAN_STEP})
    t0 = time.monotonic()
    plan = THINK_RE.sub("", chat(messages, tools=False).get("content") or "").strip()
    fast = time.monotonic() - t0 < 30  # a fast endpoint (the grader streams fast) affords more candidates
    log("PLAN:", plan[:1500])
    messages.append({"role": "assistant", "content": plan})
    base = os.path.basename(path)
    # Tests and several candidate programs, all generated in parallel from the plan (the candidates don't see the
    # tests, so a misread rule in one rarely repeats in the other); the harness keeps the candidate passing most tests.
    # Two test sets: normal behaviour, and (separately, or the model skips them) rejected input and errors.
    # 3 at a time: on a slow endpoint the edge tests would delay the second candidate past the time limit.
    asks = [(prompts.TESTS_STEP % path, 0), (prompts.TESTS_STEP % path + prompts.EDGE_TESTS, 0) if fast else None] + \
        [(prompts.CODE_STEP % (path, prompts.PROG_TIPS), t) for t in (0,) + (0.7,) * (5 if fast else 2)]
    gen = lambda a: THINK_RE.sub("", chat(messages + [{"role": "user", "content": a[0]}], tools=False, temp=a[1],
                                          max_tokens=1500 if a[0].startswith("Step 2 - write 10") else None
                                          ).get("content") or "")
    asks = [a for a in asks if a]
    tests_reply, *replies = gather(gen, asks, need=2 if fast else 1, workers=3)  # the course asks to keep concurrency low
    edge_reply = replies.pop(0) if fast else ""
    tests = parse_tests(tests_reply, base)
    tests += [t for t in parse_tests(edge_reply, base) if t[0].split("\n")[-1] not in [u[0].split("\n")[-1] for u in tests]]
    S.SPEC_TESTS[:] = tests = tests + real_data_tests(tests)
    S.REORDERED[:] = reordered(tests)
    log("REORDERED:", [c.split("\n")[-1] for _, c in S.REORDERED])
    log("TESTS REPLY:", tests_reply[:3000])
    log("EDGE TESTS REPLY:", edge_reply[:3000])
    log("TESTS:", [(c.split("\n")[-1], o, rc) for c, o, rc in tests])
    messages.append({"role": "user", "content": asks[-1][0]})
    codes, reply = [], replies[0]
    for r in replies:
        blocks = CODE_BLOCK_RE.findall(r)
        if blocks and max(blocks, key=len).strip():
            codes.append(ensure_main_call(add_imports(fix_keyword_attrs(max(blocks, key=len)))))
    code, res, outs = pick_best(base, codes, tests) if codes else ("", [], [])
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
    S.VERSIONS.setdefault(path, []).append(code)  # restorable even if the first edit breaks it before any run
    messages.append({"role": "assistant", "content": "```python\n%s```" % code})
    if S.DEADLINE - time.monotonic() > 150:  # the review costs ~40 s on a slow endpoint; then testing matters more
        messages.append({"role": "user", "content": prompts.REVIEW_STEP})
        review = THINK_RE.sub("", chat(messages, tools=False).get("content") or "").strip()
        log("REVIEW:", review[-1500:])
        messages.append({"role": "assistant", "content": review})
    messages.append({"role": "user", "content": prompts.FIX_STEP % (path, code.count("\n") + 1,
                                                                     syntax_note(path) + failures(tests, res, outs))})
    S.ACTIONS.append("write_program(%s)" % path)


def best_version(d):
    """Re-run the model's own tests on every version of the program it ran; keep the one passing most (ties: newest).
    Late edits often break things that worked."""
    vers, base = S.VERSIONS.get(d, [])[-6:], os.path.basename(d)
    try:
        with open(safe_path(d), encoding="utf-8", errors="replace") as fh:
            cur = fh.read()
    except OSError:
        return
    if cur not in vers:  # edits nobody ran (often the last ones, when time ran out): they compete too
        vers = vers[-5:] + [cur]
    tests = list(S.TESTS.items())[-30:]
    spec = S.SPEC_TESTS[:]
    if len(vers) < 2 or not tests and not spec:
        return
    reord = [c for _, c in S.REORDERED]
    outs = run_versions(base, vers, [c for c, _ in tests] + [t[0] for t in spec] + reord, 10,
                        min(time.monotonic() + 40, S.DEADLINE - 5))
    if any(len(o) < len(tests) + len(spec) + len(reord) for o in outs):
        log("version check out of time")
        return
    passed = lambda c, exp, r: r[0] is not None and (matches(exp, r[1], r[2]) or real_data(c) and same_format(exp, r[1], r[2]))
    scores = [sum(passed(c, exp, r) for (c, exp), r in zip(tests, o)) + sum(map(passes, spec, o[len(tests):]))
              - 3 * sum(crashed(r) or parsed_nothing(t, r) for t, r in zip(spec, o[len(tests):]))
              - 3 * len(order_bugs(spec, o[len(tests):])) for o in outs]
    best = max(range(len(vers)), key=lambda i: (scores[i], i))
    log("VERSION SCORES:", scores)
    if vers[best] != cur and scores[best] > scores[vers.index(cur)]:
        with open(safe_path(d), "w", encoding="utf-8", newline="") as fh:
            fh.write(vers[best])
        log("kept version %d of %s (passes %d of its own tests)" % (best, d, scores[best]))


def compiles(code):
    try:
        compile(code, "<version>", "exec")
        return True
    except (SyntaxError, ValueError):
        return False


def restore_good():
    """Code mode: never hand in a program that is worse than one we already had (edits often break working code)."""
    for d, vers in S.VERSIONS.items():
        ok = [v for v in vers if compiles(v)]
        good = [v for v in ok if v not in S.CRASHED] or ok  # crashing beats not compiling
        try:
            with open(safe_path(d), encoding="utf-8", errors="replace") as fh:
                cur = fh.read()
            if fix_keyword_attrs(cur) != cur:  # an edit introduced args.from
                cur = fix_keyword_attrs(cur)
                with open(safe_path(d), "w", encoding="utf-8", newline="") as fh:
                    fh.write(cur)
            if good and cur != good[-1] and (syntax_note(d) or cur in S.CRASHED):
                with open(safe_path(d), "w", encoding="utf-8", newline="") as fh:
                    fh.write(good[-1])
                log("restored last working version of", d)
        except OSError as e:
            log("restore failed:", e)
        best_version(d)

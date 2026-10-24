"""Debugging tasks: several independent investigations, per-key majority vote."""
import copy
import json
import os
import re
import time

from . import state as S
from .guards import bad_shape, key_specs
from .llm import THINK_RE, chat
from .loop import loop
from .util import Finish, log, safe_path


def evidence(value, limit=4):
    """Log lines (of the files present at start) that contain value."""
    out = []
    for f in sorted(S.EXISTING):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for n, line in enumerate(fh, 1):
                    if re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(value), line):
                        out.append("%s:%d: %s" % (os.path.basename(f), n, line.strip()[:250]))
                        if len(out) >= limit:
                            return out
        except OSError:
            pass
    return out


def choose(key, vals, specs):
    """Show the model the log lines of each candidate value of key and let it pick one; None if it can't."""
    ev = [(v, evidence(v)) for v in vals]
    if not all(e for _, e in ev):
        return None
    q = "\n\n".join("Option %d: %s\n%s" % (i + 1, v, "\n".join(e)) for i, (v, e) in enumerate(ev))
    msg = [{"role": "user", "content": "%s\n\nQuestion: `%s` - %s\n\nCandidate answers and the log lines that "
            "mention them:\n\n%s\n\nWhich option answers the question for THIS request? Think about which step "
            "failed, timed out or was still pending/waiting when the request died. End with the line ANSWER: <option "
            "number>." % (S.PROMPT[:6000], key, specs.get(key, ""), q)}]
    try:
        reply = THINK_RE.sub("", chat(msg, tools=False, max_tokens=600).get("content") or "")
    except Exception as e:  # noqa: BLE001 - includes Finish (timeout): keep what we have
        log("adjudication failed:", e)
        return None
    m = re.findall(r"ANSWER:\s*(?:Option\s*)?(\d+)", reply)
    log("ADJUDICATE %s: %s -> %s" % (key, vals, m[-1] if m else reply[-200:]))
    return vals[int(m[-1]) - 1] if m and 1 <= int(m[-1]) <= len(vals) else None


def is_id(v):
    return isinstance(v, str) and len(v) >= 4 and bool(re.search(r"\d", v))


def adjudicate(reports, specs):
    """The investigations disagree on an ID: let the model pick between them for the first such key (keys follow the
    request's chain, so later keys depend on it). Majority alone fails when two runs make the same tempting mistake.
    Returns the reports that agree with the chosen value."""
    for key in reports[0]:
        vals = list(dict.fromkeys(r.get(key) for r in reports))
        if len(vals) >= 2 and all(is_id(v) for v in vals):
            pick = choose(key, vals, specs)
            return [r for r in reports if r.get(key) == pick] if pick else reports
    return reports


def challenge(report, specs):
    """Even agreeing runs pick the first matching ID: when the previous key's value (e.g. the trace) appears with
    another ID of the same shape as this key's value (a second ticket of that trace), let the model choose.
    Returns (key, wrong value, right value) for the first key where it chose differently, else None."""
    keys = list(report)
    for prev, key in zip(keys, keys[1:]):
        v, p = report[key], report[prev]
        if not (is_id(v) and is_id(p)):
            continue
        shape = re.sub(r"\d", r"\\d", re.escape(v))
        alts = [a for line in evidence(p, 40) for a in re.findall(r"(?<![\w-])%s(?![\w-])" % shape, line)]
        alts = [a for a in dict.fromkeys(alts) if a != v][:3]
        if alts:
            pick = choose(key, [v] + alts, specs)
            return (key, v, pick) if pick and pick != v else None
    return None


def repair(report, specs):
    """Keys left empty or of the wrong shape (a task ID where a class name is asked): show the model the log lines of
    the IDs found for the keys before them (the bad value itself first) and take each answer that appears verbatim
    in those lines and has the right shape. One model call."""
    keys = list(report)
    broken = [k for k in keys if report[k] in ("", None, []) or bad_shape(specs.get(k, ""), report[k])]
    if not broken or len(broken) > len(keys) // 2:
        return report
    lines = []
    for k in broken:
        i = keys.index(k)
        for v in [report[k]] + [report[j] for j in reversed(keys[:i])]:
            if is_id(v) and len(lines) < 60:
                lines += [l for l in evidence(v, 25) if l not in lines]
    if not lines:
        return report
    ask = "\n".join("- `%s`: %s" % (k, specs.get(k, "")) for k in broken)
    msg = [{"role": "user", "content": "%s\n\nFacts already established: %s\n\nLog lines about these IDs:\n%s\n\n"
            "Answer these keys from the log lines above, each value copied exactly as written there:\n%s\n\nReply with "
            "one JSON object with exactly these keys." % (S.PROMPT[:6000], json.dumps({k: v for k, v in report.items()
                                                                                    if k not in broken}),
                                                          "\n".join(lines[:60]), ask)}]
    try:
        reply = THINK_RE.sub("", chat(msg, tools=False, max_tokens=500).get("content") or "")
        got = json.loads(re.search(r"\{.*\}", reply, re.S).group(0))
    except Exception as e:  # noqa: BLE001 - includes Finish (timeout) and an unparsable reply
        log("repair failed:", e)
        return report
    text = "\n".join(lines)
    for k in broken:
        v = got.get(k)
        if isinstance(v, str) and v.isdigit() and "(integer)" in specs.get(k, ""):
            v = int(v)
        if v not in ("", None) and str(v) in text and not bad_shape(specs.get(k, ""), v):
            log("REPAIRED %s: %r -> %r" % (k, report[k], v))
            report[k] = v
    return report


def vote_runs(base, sysmsg, path):
    """Debugging tasks: investigate up to 3 times from scratch and take the per-key majority (single runs of a small
    model vary a lot: one wrong hop early on spoils every later key). Ends by raising Finish."""
    final, reports, last, hint, specs = S.DEADLINE, [], None, None, key_specs()
    for k in range(3):
        if k == 2 and len(reports) >= 2 and reports[0] == reports[-1]:
            break  # two independent investigations agree
        if k and (final - time.monotonic() < 0.3 * (final - S.START) or S.CALLS > 70):
            break
        S.NOTES[:] = []
        S.BOUNCES.clear()
        if k and os.path.isfile(safe_path(path)):
            os.remove(safe_path(path))
        S.DEADLINE = time.monotonic() + (final - 25 - time.monotonic()) * (0.55 if k == 0 else 0.6 if k == 1 else 1)
        try:
            msgs = copy.deepcopy(base)
            if hint:
                msgs[-1]["content"] += ("\n\n[Checked by the harness against the logs: `%s` is %s, NOT %s. Use %s and "
                                        "follow the chain on from it.]" % (hint[0], hint[2], hint[1], hint[2]))
            loop(msgs, sysmsg, max(5, min(S.MAX_TURNS, 110 - S.CALLS)), 0 if k == 0 else 0.6)
        except Finish as f:
            last = f
        finally:
            S.DEADLINE = final
        try:
            with open(safe_path(path), encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict) and d:
                reports.append(d)
                log("ATTEMPT %d REPORT: %s" % (k + 1, json.dumps(d)[:600]))
                if not hint and final - time.monotonic() > 60:
                    hint = challenge(d, specs)
                    log("CHALLENGE:", hint)
        except (OSError, ValueError):
            log("ATTEMPT %d: no usable %s" % (k + 1, path))
    if not reports:
        raise last or Finish("error", "No %s was produced." % path)
    if hint:
        reports = [r for r in reports if r.get(hint[0]) == hint[2]] or reports
    voted = {}
    reports = adjudicate(reports, specs) if len(reports) > 1 else reports
    for key in [k for r in reports for k in r]:
        if key not in voted:
            vals = [json.dumps(r[key], sort_keys=True) for r in reports if key in r]
            vals = [v for v in vals if v not in ('""', "null")] or vals  # an attempt that ran out of time
            vals = [v for v in vals if not bad_shape(specs.get(key, ""), json.loads(v))] or vals  # drop misfits
            voted[key] = json.loads(max(vals, key=lambda v: (vals.count(v), -vals.index(v))))  # ties: earliest run
    if final - time.monotonic() > 10:
        voted = repair(voted, specs)
    with open(safe_path(path), "w", encoding="utf-8") as fh:
        json.dump(voted, fh, indent=2, ensure_ascii=False)
    raise Finish("success", "Wrote %s (per-key majority of %d independent investigations): %s" % (
        path, len(reports), json.dumps(voted, ensure_ascii=False)))

"""The tool-calling loop."""
import json
import os
import time

from . import state as S
from .guards import deliverables_exist
from .llm import THINK_RE, chat, extract_calls, valid_args
from .tools import run_tool
from .util import Finish, log, trace


def overview():
    """Code mode: sizes and first lines of the workspace files, so the model sees data/log formats up front."""
    out = []
    for r, ds, fs in os.walk(S.WORKSPACE):
        ds.sort()
        for f in sorted(fs):
            fp = os.path.join(r, f)
            rel = os.path.relpath(fp, S.WORKSPACE)
            if rel == "TASK.md" or len(out) >= 30:
                continue
            try:
                with open(fp, encoding="utf-8", errors="replace") as fh:
                    head = [next(fh, "").rstrip("\n")[:250] for _ in range(3)]
                with open(fp, "rb") as fh:
                    n = sum(1 for _ in fh)
            except OSError:
                continue
            out.append("%s (%d bytes, %d lines), first lines (format sample only, unrelated to the task):\n%s" % (rel, os.path.getsize(fp), n,
                       "\n".join("    " + h for h in head if h)))
    return "\n".join(out) or "(no files besides TASK.md)"


def describe(name, a):
    if name == "write_file":
        a = {"path": a.get("path"), "content": str(a.get("content", ""))[:200]}
    return ", ".join("%s=%s" % (k, v if isinstance(v, str) else json.dumps(v)) for k, v in a.items())


def loop(messages, sysmsg, max_turns, base_temp=0):
    """The tool-calling loop; ends by raising Finish."""
    S.TEMP, t0 = base_temp, time.monotonic()
    nudged = False
    stuck = 0
    blind = set()  # write_file calls made before the model saw what it had read
    seen = {}  # code mode: identical look-ups already made
    hurried = 0
    for turn in range(max_turns):
        left = S.DEADLINE - time.monotonic()
        if S.CODE_MODE and S.DELIV and not deliverables_exist() and hurried < 2 and (
                left < (S.DEADLINE - t0) * (0.5 if hurried == 0 else 0.25) or turn >= max_turns * (0.5 if hurried == 0 else 0.8)):
            hurried += 1
            messages.append({"role": "user", "content": "Time check: about %ds and %d steps left. Write %s NOW with the "
                             "best values you have found so far (you can still refine it afterwards)." % (
                                 left, max_turns - turn, ", ".join(sorted(S.DELIV)))})
        if S.NOTES:
            messages[0]["content"] = sysmsg + "\nYour notes so far:\n" + "\n".join("- " + n for n in S.NOTES)
        msg = chat(messages)
        content = THINK_RE.sub("", msg.get("content") or "").strip()
        calls = extract_calls(msg)
        log("MODEL:", content[:500], [(n, a) for _, n, a in calls])
        if not calls:
            if nudged and (content or not S.CODE_MODE):
                raise Finish("success", content)
            if nudged:  # code mode, empty reply again: resample hotter instead of quitting with nothing
                S.TEMP = min(1.0, S.TEMP + 0.3)
            nudged = True
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": "If the task is done, call finish now with your full answer (exact numbers/facts) in message; otherwise use the tools to do it."})
            continue
        messages.append({"role": "assistant", "content": content, "tool_calls": [
            {"id": cid, "type": "function", "function": {
                "name": name, "arguments": valid_args(args)}}
            for cid, name, args in calls]})
        looked = False
        repeats = 0
        ran = 0
        for cid, name, args in calls:
            try:
                k = json.loads(args) if isinstance(args, str) else dict(args)
                k = name + json.dumps({a: b for a, b in k.items() if a not in ("why", "expect")}, sort_keys=True)
            except (ValueError, TypeError, AttributeError):
                k = name + str(args)
            if name != "edit_file":  # an identical edit again only fails again or inserts its lines twice
                k += "@%d" % S.EDITS  # after a write, reading or running the same thing again is fine
            if S.CODE_MODE and name == "run_command":
                ran += 1
            if ran > 5 and name == "run_command":
                result = "SKIPPED: at most 5 commands per step. Check the results above first, then run more."
            elif looked and name not in S.LOOK_TOOLS:
                # The model batched an action with a read it hasn't seen yet (it would be guessing).
                result = "SKIPPED: look at the results above first, then call %s again." % name
                if name == "write_file":
                    blind.add(args if isinstance(args, str) else json.dumps(args, sort_keys=True))
            elif name == "write_file" and (args if isinstance(args, str) else json.dumps(args, sort_keys=True)) in blind:
                blind.clear()  # reject the unchanged blind write once; a second identical write goes through
                looked = True  # and skip whatever was batched after it (e.g. finish)
                result = ("REJECTED: this is the same content you wrote before seeing the file contents. Base the "
                          "content only on the file contents shown above, then call write_file again.")
            elif S.CODE_MODE and name in ("search_file", "read_file", "run_command", "edit_file") and seen.get(k, 0) >= 1:
                seen[k] += 1
                repeats += 1
                result = ("REPEATED EDIT: you already made this exact edit; it did not fix the failing test, so the bug "
                          "is elsewhere. Read the code that handles the failing command again and make a DIFFERENT edit."
                          if name == "edit_file" else
                          "REPEATED CALL: you already made this exact call and no file has changed since, so the result "
                          "is the same. Do not repeat calls. " + ("Fix the program (edit_file) or your test, then run it "
                          "again." if name == "run_command" else "Use what you found, try a different search, or write "
                          "the deliverable now."))
            else:
                seen[k] = seen.get(k, 0) + 1
                try:
                    parsed = json.loads(args) if isinstance(args, str) else args
                    parsed = parsed if isinstance(parsed, dict) else {}
                    result = run_tool(name, parsed)
                    if name != "finish":
                        S.ACTIONS.append("%s(%s) -> %s" % (name, describe(name, parsed), result[:400]))
                except ValueError:
                    result = "ERROR: arguments were not valid JSON"
            looked = looked or name in S.LOOK_TOOLS
            if S.CODE_MODE and name != "finish":
                S.SEEN.append(result)
            log("TOOL %s -> %s" % (name, result[:200]))
            trace(tool=name, args=args, result=result)
            messages.append({"role": "tool", "tool_call_id": cid, "content": result})
        if S.CODE_MODE and repeats == len(calls):
            # Stuck in a loop: drop the repeat from history and sample with some randomness to break out.
            del messages[-1 - len(calls):]
            stuck += 1
            S.TEMP = min(1.0, 0.5 + 0.25 * stuck)
            if stuck % 4 == 0:
                messages.append({"role": "user", "content": "You are going in circles repeating the same calls. Look at "
                                 "your notes and results so far, then either search for something NEW (another ID form, "
                                 "another file) or write the deliverable with what you have."})
        else:
            S.TEMP = base_temp
    raise Finish("error", "Step limit reached without finishing.")

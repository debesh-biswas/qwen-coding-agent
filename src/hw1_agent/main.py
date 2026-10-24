"""Entry point: one prompt in, exactly one JSON object {"status", "message"} on stdout. Logs go to stderr."""
import json
import os
import re
import sys

from . import prompts
from . import state as S
from .debugging import vote_runs
from .guards import deliverables_exist
from .loop import loop, overview
from .programming import restore_good, write_program
from .session import load_session, save_session
from .tools.schemas import CODE_TOOLS, TOOLS
from .util import Finish

CODE_RE = re.compile(r"```|REPORT\.json", re.I)  # CP1 log-summary prompts must stay in normal mode


def run(prompt):
    S.PROMPT = prompt
    S.TOOLS_ACTIVE = TOOLS
    sysmsg = prompts.SYSTEM_PROMPT
    if len(prompt) > 1500 or CODE_RE.search(prompt):
        S.CODE_MODE, S.MAX_TURNS = True, 100
        S.DEADLINE = S.START + float(os.environ.get("HW1_DEADLINE_S") or 285)
        S.TOOLS_ACTIVE = [t for t in TOOLS if t["function"]["name"] != "read_file"] + CODE_TOOLS
        S.LOOK_TOOLS += ("search_file", "run_command")
        sysmsg += prompts.CODE_PROMPT
        S.EXISTING = {os.path.realpath(os.path.join(r, f)) for r, _, fs in os.walk(S.WORKSPACE) for f in fs}
        S.DELIV = {m.group(0) for m in re.finditer(r"\b[\w.-]+\.(?:py|json)\b", prompt)
                   if not os.path.exists(os.path.join(S.WORKSPACE, m.group(0)))}
    S.GUARD = "\n".join([prompt] + [t["user"] + "\n" + t["assistant"] for t in S.TURNS])
    messages = [{"role": "system", "content": sysmsg}]
    for t in S.TURNS[-20:]:
        messages += [{"role": "user", "content": t["user"]}, {"role": "assistant", "content": t["assistant"]}]
    messages.append({"role": "user", "content": prompt + (
        "\n\n[Workspace overview from the harness]\n" + overview() if S.CODE_MODE else "")})
    py = sorted(d for d in S.DELIV if d.endswith(".py"))
    js = sorted(d for d in S.DELIV if d.endswith(".json"))
    if S.CODE_MODE and py:
        S.RESERVE = 30  # stop model calls early so the final version check (restore_good) gets time to run
        S.DEADLINE -= S.RESERVE
        write_program(messages, py[0])
    elif S.CODE_MODE and js:
        vote_runs(messages, sysmsg, js[0])
    loop(messages, sysmsg, S.MAX_TURNS)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    if len(sys.argv) > 2:
        S.SESSION_ID = sys.argv[2].strip()
    if S.SESSION_ID:
        load_session()
    try:
        missing = [k for k in ("OPENAI_BASE_URL", "MODEL", "QWEN_API_KEY") if not os.environ.get(k)]
        if missing:
            raise Finish("error", "Missing environment variables: " + ", ".join(missing))
        if len(sys.argv) < 2 or not sys.argv[1].strip():
            raise Finish("error", "No prompt given.")
        run(sys.argv[1])
    except Finish as f:
        status, message = f.status, f.message
        if S.CODE_MODE and status == "error" and message in ("Timed out waiting for the model.", "Step limit reached without finishing."):
            got = deliverables_exist()
            if got:
                status, message = "success", "Wrote %s (step/time limit reached)" % ", ".join(got)
    except Exception as e:  # never let a crash break the output contract
        status, message = "error", "Internal error: %s" % e
    if S.CODE_MODE:
        S.DEADLINE += S.RESERVE
        restore_good()
    if S.SESSION_ID and len(sys.argv) > 1 and sys.argv[1].strip():
        save_session(sys.argv[1], status, message)
    print(json.dumps({"status": status, "message": message}, ensure_ascii=False))
    sys.exit(0 if status == "success" else 1)

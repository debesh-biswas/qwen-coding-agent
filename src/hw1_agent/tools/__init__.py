"""Tool dispatch: run_tool executes one model tool call and returns its result text."""
import ast
import re
import time

from .. import state as S
from ..guards import DELETE_RE, blocked, deliverables_exist, finish_check, unseen
from ..util import Finish, safe_path
from .csv_tools import calc, csv_tool, fmt
from .files import copy_file, delete_file, edit_file, list_files, read_file, search_file, write_file
from .shell import run_checked

PLACEHOLDER_RE = re.compile(r"<elided: \d+ chars|\[older output elided|\[program text elided")
FILE_TOOLS = {"list_files": list_files, "read_file": read_file, "write_file": write_file, "delete_file": delete_file,
              "copy_file": copy_file}
CODE_ONLY = {"run_command": run_checked, "search_file": search_file, "edit_file": edit_file}


def finish(args):
    """Raise Finish, unless code mode has a reason to send the model back to work."""
    status = args.get("status", "success")
    if S.CODE_MODE and status == "error" and S.DELIV and not deliverables_exist() and S.BOUNCES.get("giveup", 0) < 2:
        S.BOUNCES["giveup"] = S.BOUNCES.get("giveup", 0) + 1
        return ("ERROR: do not give up without the deliverable: partial answers still score. Write %s now with "
                "the best values you have (try other search patterns for missing ones), then finish." % ", ".join(sorted(S.DELIV)))
    if (S.CODE_MODE and status == "error" and any(d.endswith(".py") for d in S.DELIV) and deliverables_exist()
            and S.DEADLINE - time.monotonic() > 0.3 * (S.DEADLINE - S.START) and S.BOUNCES.get("quit", 0) < 4):
        S.BOUNCES["quit"] = S.BOUNCES.get("quit", 0) + 1
        return ("ERROR: do not give up, there is time left and every requirement that works still scores. Read the "
                "part of the program that produces the wrong output (read_file with start_line), find the bug, "
                "fix it (edit_file, or write_file to rewrite the file), re-run your tests, then finish.")
    if S.CODE_MODE and status == "success":
        bad = finish_check()
        if bad:
            return bad
    raise Finish(status if status in ("success", "error") else "success", str(args.get("message", "")))


def note(args):
    if len(S.NOTES) >= 30:
        return "ERROR: note limit (30) reached"
    bad = unseen(args.get("text", ""))
    if bad:
        return ("NOT NOTED: %s never appeared in any tool output, so it is a guess. Only note values you have seen; "
                "find these with search_file first." % ", ".join(bad))
    S.NOTES.append(str(args.get("text", ""))[:300])
    return "Noted (%d notes)" % len(S.NOTES)


def run_tool(name, args):
    """Execute one tool call; returns a result string (errors are returned, not raised)."""
    try:
        if name == "finish":
            return finish(args)
        safe_path(args.get("path", "."))  # any path outside the workspace is an error, whatever the tool
        dest = {"write_file": "path", "delete_file": "path", "csv_update": "output", "csv_remove_rows": "output",
                "copy_file": "dest", "edit_file": "path"}.get(name)
        if name == "delete_file" and not DELETE_RE.search(S.PROMPT):
            return "ERROR: the user did not ask to delete anything in this request; do not delete files"
        err = dest and blocked(str(args.get(dest, "")))
        if err:
            return err
        if name in ("write_file", "edit_file") and PLACEHOLDER_RE.search(str(args.get("content", "")) + str(args.get("new", ""))):
            return ("ERROR: that text contains a harness placeholder, not real content. read_file the current file and "
                    "write the real text.")
        if S.CODE_MODE and name in CODE_ONLY:
            return CODE_ONLY[name](args)
        if S.CODE_MODE and name == "note":
            return note(args)
        if name in FILE_TOOLS:
            return FILE_TOOLS[name](args)
        if name.startswith("csv_"):
            return csv_tool(name, args)
        if name == "calculate":
            return fmt(calc(ast.parse(str(args.get("expression", "")).strip(), mode="eval")))
        return "ERROR: unknown tool %s" % name
    except Finish:
        raise
    except Exception as e:
        return "ERROR: %s" % e

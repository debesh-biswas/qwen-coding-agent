"""Session memory: earlier turns per (workspace, session ID), stored outside the workspace."""
import hashlib
import json
import os
import tempfile

from . import state as S
from .util import log


def session_file():
    d = os.environ.get("HW1_SESSION_DIR") or os.path.join(tempfile.gettempdir(), "hw1-agent-sessions")
    return d, hashlib.sha256((S.WORKSPACE + "\0" + S.SESSION_ID).encode()).hexdigest()[:32] + ".json"


def load_session():
    try:
        d, f = session_file()
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            S.TURNS = [t for t in json.load(fh)["turns"] if isinstance(t.get("user"), str) and isinstance(t.get("assistant"), str)]
    except Exception:
        S.TURNS = []


def save_session(prompt, status, message):
    try:
        d, f = session_file()
        record = "Actions I took: %s\nFinal answer (%s): %s" % ("; ".join(S.ACTIONS) or "(none)", status, message)
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, f + ".%d.tmp" % os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"session": S.SESSION_ID, "workspace": S.WORKSPACE,
                       "turns": (S.TURNS + [{"user": prompt, "assistant": record}])[-20:]}, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(d, f))
    except Exception as e:
        log("could not save session:", e)

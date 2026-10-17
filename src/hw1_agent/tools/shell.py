"""run_command: shell commands in the workspace, without the API key in their environment."""
import functools
import os
import signal
import subprocess
import sys
import tempfile
import time

from .. import state as S
from ..util import cap, safe_env, safe_path


@functools.lru_cache(maxsize=None)
def python_shim():
    """A dir where `python` runs this interpreter: models often type `python`, which many systems lack."""
    d = tempfile.mkdtemp(prefix="hw1-bin-")
    try:
        os.symlink(sys.executable, os.path.join(d, "python"))
    except OSError:
        pass
    return d


def run_command(a):
    """Returns (exit code, stdout, stderr); raises on timeout (run_tool turns that into an ERROR result)."""
    t = min(float(a.get("timeout") or 60), 120, S.DEADLINE - time.monotonic() - 15)
    if t < 1:
        raise RuntimeError("not enough time left to run commands; finish now")
    env = safe_env()
    env["PATH"] = python_shim() + os.pathsep + env.get("PATH", "")
    p = subprocess.Popen(str(a.get("command", "")), shell=True, cwd=S.WORKSPACE, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                         errors="replace", env=env, start_new_session=True)
    try:
        out, err = p.communicate(timeout=t)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        raise RuntimeError("command timed out after %ds" % t)
    return p.returncode, out, err


def matches(exp, so, se):
    flat = lambda t: " ".join(t.split()).lower()
    return flat(exp) in (flat(so), flat(so + se), flat(se))


def run_checked(a):
    """Code mode run_command: label the output CRASH / MATCH / DIFFERENT against the model's own `expect`, and record
    the program versions and test results that finish_check and best_version use."""
    S.UNTESTED.difference_update([f for f in list(S.UNTESTED) if f in str(a.get("command", ""))])
    rc, so, se = run_command(a)
    exp = str(a.get("expect") or "")
    verdict = ("CRASH: the program raised an exception - fix it." if "Traceback (most recent call last)" in se
               else "MATCH: same as you expected." if exp and matches(exp, so, se)
               else "DIFFERENT from what you expected - work out which is right per the spec and fix the program "
                    "if it is wrong." if exp else "")
    out = "%s\nYou expected: %s\nexit=%d\n--- stdout ---\n%s\n--- stderr ---\n%s" % (
        verdict, cap(exp, 1000) or "(nothing stated)", rc, cap(so, 2000), cap(se, 2000))
    c = str(a.get("command", ""))[:100]
    for d in S.DELIV:
        if d.endswith(".py") and os.path.basename(d) in c and os.path.isfile(safe_path(d)):
            with open(safe_path(d), encoding="utf-8", errors="replace") as fh:
                v = fh.read()
            vers = S.VERSIONS.setdefault(d, [])
            if v in vers:
                vers.remove(v)
            vers.append(v)  # most recently run last
            if exp:
                S.TESTS[str(a.get("command", ""))] = exp
            (S.CRASHED.add if verdict.startswith("CRASH") else S.CRASHED.discard)(v)
    if verdict.startswith(("DIFFERENT", "CRASH")):
        S.OPEN_FAILS.append(c)
    elif verdict.startswith("MATCH"):
        S.OPEN_FAILS[:] = [f for f in S.OPEN_FAILS if f != c]
    return out

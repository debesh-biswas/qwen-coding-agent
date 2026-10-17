"""Small shared helpers: the Finish signal, logging, workspace path guard."""
import os
import sys

from . import state as S


class Finish(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def safe_path(p):
    full = os.path.realpath(os.path.join(S.WORKSPACE, p or "."))
    if full != S.WORKSPACE and not full.startswith(S.WORKSPACE + os.sep):
        raise ValueError("path %r is outside the workspace" % p)
    return full


def safe_env():
    """os.environ without the API key, for every subprocess."""
    return {k: v for k, v in os.environ.items() if k != "QWEN_API_KEY"}


def cap(s, n=4000):
    return s if len(s) <= n else "%s\n...[%d chars cut]...\n%s" % (s[:n // 2], len(s) - n, s[-(n // 2):])

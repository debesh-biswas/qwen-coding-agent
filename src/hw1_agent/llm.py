"""Model client: streaming chat completions with retries, context compaction, tool-call parsing."""
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import state as S
from .util import Finish, log


def est(messages):
    return len(json.dumps(messages + S.TOOLS_ACTIVE)) // 3  # conservative token estimate


def compact(messages, target):
    """Elide old bulky tool output / written file bodies (keeps system, current prompt, last 6 messages)."""
    keep = {0, len(messages) - 1} | set(range(max(0, len(messages) - 6), len(messages)))
    keep |= {i for i, m in enumerate(messages) if m["role"] == "user" and m["content"] == S.PROMPT}
    for i, m in enumerate(messages):
        if est(messages) <= target:
            return
        if i in keep:
            continue
        if m["role"] == "assistant" and len(m.get("content") or "") > 2000 and m["content"].startswith("```"):
            m["content"] = "[program text elided; the current version is on disk - read_file it if needed]"
        if m["role"] == "tool" and len(m["content"]) > 400:
            m["content"] = m["content"][:300] + "\n[older output elided to save context; re-run the tool if you need it]"
        for c in m.get("tool_calls") or []:
            f = c["function"]
            k = {"write_file": "content", "edit_file": "new"}.get(f["name"])
            try:
                a = json.loads(f["arguments"])
                if k and isinstance(a.get(k), str) and len(a[k]) > 400:
                    a[k] = "<elided: %d chars, already written to disk>" % len(a[k])
                    f["arguments"] = json.dumps(a)
            except (ValueError, AttributeError):
                pass
    # Still too big: the recent messages themselves are bulky (long command output, program text). Trim them too,
    # keeping only the newest two messages whole.
    for m in messages[1:-2]:
        if est(messages) <= target:
            return
        if m["role"] in ("tool", "assistant") and len(m.get("content") or "") > 1500 and m.get("content") != S.PROMPT:
            m["content"] = m["content"][:700] + "\n[...elided to save context...]\n" + m["content"][-500:]
        for c in m.get("tool_calls") or []:
            if len(c["function"]["arguments"]) > 1500:
                c["function"]["arguments"] = valid_args(json.dumps({"note": "arguments elided to save context"}))
    # Last resort: drop the oldest exchanges (an assistant call plus its tool results) after the task prompt.
    first = next((i for i, m in enumerate(messages) if m["role"] == "assistant" and m.get("tool_calls")), None)
    while first is not None and est(messages) > target and len(messages) - first > 4:
        j = first + 1
        while j < len(messages) and messages[j]["role"] == "tool":
            j += 1
        del messages[first:j]
        if first < len(messages) and messages[first]["role"] != "assistant":
            first = next((i for i in range(first, len(messages)) if messages[i]["role"] == "assistant" and
                          messages[i].get("tool_calls")), None)


def read_stream(r):
    """Assemble a streamed (SSE) chat completion into a normal message. Streaming keeps long generations alive
    behind proxies that cut idle requests (Cloudflare 524 after ~100 s)."""
    content, calls = [], {}
    for line in r:
        if time.monotonic() > S.DEADLINE:
            raise Finish("error", "Timed out waiting for the model.")
        line = line.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        obj = json.loads(data)
        if obj.get("error"):
            raise OSError("stream error: %s" % str(obj["error"])[:200])
        for ch in obj.get("choices") or []:
            d = ch.get("delta") or {}
            content.append(d.get("content") or "")
            for tc in d.get("tool_calls") or []:
                c = calls.setdefault(tc.get("index", 0), {"id": None, "type": "function", "function": {"name": "", "arguments": ""}})
                c["id"] = tc.get("id") or c["id"]
                fn = tc.get("function") or {}
                c["function"]["name"] = c["function"]["name"] or fn.get("name") or ""
                c["function"]["arguments"] += fn.get("arguments") or ""
    return {"content": "".join(content), "tool_calls": [calls[i] for i in sorted(calls)] or None}


def chat(messages, tools=True):
    url = os.environ["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions"
    if est(messages) > 26000:
        compact(messages, 20000)
    shrink = [12000, 8000, 5000]  # context-overflow retries
    log("context estimate: %d tokens, %d messages" % (est(messages), len(messages)))
    # Custom User-Agent: the course endpoint's Cloudflare blocks urllib's default one (error 1010).
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["QWEN_API_KEY"],
               "User-Agent": "hw1-agent/1.0"}
    delay = 2
    while True:
        remaining = S.DEADLINE - time.monotonic()
        if remaining < 3:
            raise Finish("error", "Timed out waiting for the model.")
        body = json.dumps(dict({
            "model": os.environ["MODEL"], "messages": messages,
            "temperature": S.TEMP, "max_tokens": max(1024, min(8192, 40000 - est(messages))),
            "chat_template_kwargs": {"enable_thinking": False}, "stream": True,
        }, **({"tools": S.TOOLS_ACTIVE, "tool_choice": "auto"} if tools else {}))).encode()
        try:
            req = urllib.request.Request(url, body, headers)
            # Per-read timeout: a stalled stream is retried instead of eating the whole budget.
            with urllib.request.urlopen(req, timeout=min(remaining, 120)) as r:
                if "event-stream" not in (r.headers.get("Content-Type") or ""):
                    out = json.load(r)["choices"][0]["message"]
                else:
                    out = read_stream(r)
                S.CALLS += 1
                return out
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            err = raw[:300]
            if e.code == 400 and shrink and re.search(r"context|token|length", raw[:2000], re.I):
                compact(messages, shrink.pop(0))
                continue
            # An HTML error page (not a JSON API error) is a transient proxy/backend glitch: retry it.
            if e.code != 429 and e.code < 500 and not err.lstrip().startswith("<"):
                raise Finish("error", "Model API error %d: %s" % (e.code, err))
            log("HTTP %d, retrying: %s" % (e.code, err[:80].replace("\n", " ")))
        except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as e:
            # HTTPException covers IncompleteRead / RemoteDisconnected mid-stream; ValueError a garbled chunk.
            log("connection error, retrying:", e)
        time.sleep(min(delay, max(0, S.DEADLINE - time.monotonic() - 3)))
        delay = min(delay * 2, 10)


TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
THINK_RE = re.compile(r"<think>.*?</think>", re.S)


def extract_calls(msg):
    """Return [(id, name, raw_args)] from structured tool_calls, or Qwen's raw <tool_call> text as fallback."""
    calls = [(c.get("id") or "call_%d" % i, c["function"]["name"], c["function"].get("arguments") or "{}")
             for i, c in enumerate(msg.get("tool_calls") or [])]
    if calls:
        return calls
    for i, m in enumerate(TOOL_CALL_RE.finditer(msg.get("content") or "")):
        try:
            d = json.loads(m.group(1))
            calls.append(("call_%d" % i, d.get("name", ""), d.get("arguments", {})))
        except ValueError:
            pass
    return calls


def valid_args(args):
    """Echo tool-call arguments back as valid JSON: the server rejects a history holding malformed arguments."""
    if not isinstance(args, str):
        return json.dumps(args)
    try:
        json.loads(args)
        return args
    except ValueError:
        return "{}"

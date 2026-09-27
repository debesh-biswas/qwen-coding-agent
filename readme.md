# HW1 Checkpoint 1: File-Management Agent

A tool-calling agent written from scratch around Qwen3-14B. It uses only the OpenAI-compatible `/chat/completions` API, with no agent SDKs or frameworks.

## Structure

```
run_agent.sh          entry point: bash run_agent.sh "<prompt>" (cwd = task workspace)
agent.py              the agent harness (model loop, tools, JSON output)
tests/run_tests.py    test runner
tests/cases/<name>/   one test case: prompt.txt, setup/ (initial workspace files), expect.json
```

## How it works

`agent.py` sends the prompt to the model together with five tools: `list_files`, `read_file`, `write_file`, `delete_file` and `finish`. It runs each tool call the model makes, returns the result to the model and repeats until the model calls `finish`. The limits are 12 model turns and 165 s.

- Paths are checked to stay inside the current working directory. `../` and symlinks that point outside the workspace are rejected.
- If the model returns a raw `<tool_call>` in the text instead of a structured tool call, the agent parses it from the text.
- Thinking mode is turned off (`chat_template_kwargs.enable_thinking=false`), which makes responses faster.
- Stdout always gets exactly one JSON object `{"status", "message"}`, even when something crashes. Logs go to stderr. The exit code is 0 on success and 1 on error.
- On connection errors, 429 and 5xx responses, the request is retried until the deadline.

## Requirements

- Python ≥ 3.8, **standard library only** (nothing to install)
- Bash
- Env vars: `OPENAI_BASE_URL`, `MODEL`, `QWEN_API_KEY`

Tested on macOS 26 (Apple Silicon) with Python 3.14.6. Development used a local Qwen3-14B (`mlx-community/Qwen3-14B-4bit` via `mlx_lm.server`). The agent uses no platform-specific code.

## Running the tests

```bash
OPENAI_BASE_URL="https://llm.montii.me/v1" MODEL="Qwen/Qwen3-14B" QWEN_API_KEY="..." \
  python3 tests/run_tests.py [-k create] [--repeat 3]
```

Each case runs in a fresh temp workspace with a new agent process. The runner checks:

- the exit code and the JSON shape of stdout
- exact file contents and deleted files
- that unrelated files are unchanged and no extra files appear
- facts in the answer, and the summary's word limit and key facts
- that nothing is written outside the workspace

There are 25 cases covering create, delete, read-and-answer, multi-file summary and path escape. Set `VERBOSE=1` to print the agent's stderr when a case fails.

"""Run-wide state. One agent process handles one prompt, so module globals are the run's state;
other modules read and assign them as S.NAME."""
import os
import time

START = time.monotonic()
DEADLINE = START + float(os.environ.get("HW1_DEADLINE_S") or 165)  # spec limit is 180 s
MAX_TURNS = 12
CODE_MODE = False  # long programming/debugging prompts: more tools, 285 s, 100 turns
WORKSPACE = os.path.realpath(os.getcwd())
PROMPT = ""
GUARD = ""  # current prompt + earlier prompts/records of this session: files named here may be changed
SESSION_ID = ""
TURNS = []  # stored earlier turns [{"user", "assistant"}]
ACTIONS = []  # compact records of tool calls executed in this run
TOOLS_ACTIVE = []  # tool schemas sent to the model (set in main.run)
LOOK_TOOLS = ("read_file", "list_files", "csv_stats", "csv_row_sum", "calculate")
BUSY = 0  # "busy" replies (HTTP 429/503) so far: parallel requests back off (programming.gather)
CALLS = 0  # successful model calls this run (the grader allows 120)
TEMP = 0  # raised temporarily when the model is stuck repeating itself

# Code mode
EXISTING = set()  # realpaths present when a code-mode run started
WRITTEN = set()  # files written/edited this run
DELIV = set()  # file names the prompt asks for that did not exist at start
BOUNCES = {}  # finish-check complaint kind -> times used
NOTES = []
SEEN = []  # every tool result shown to the model (to catch guessed values)
UNTESTED = set()  # .py files changed since a run_command last executed them
LAST_EDIT = {}
RESERVE = 0  # seconds held back from the model loop for the final version check
EDITS = 0  # file writes so far: the same call after a write is not a repeat
CRASHED = set()  # program versions (file contents) whose latest run crashed
VERSIONS = {}  # .py deliverable -> distinct versions that were run, most recently run last
TESTS = {}  # test command -> output the model expected (its own test suite)
SPEC_TESTS = []  # (command, expected stdout or None, exit code) written before any code: programming.parse_tests
REORDERED = []  # (index into SPEC_TESTS, same call with options reordered): programming.reordered
OPEN_FAILS = []  # test commands that came back DIFFERENT/CRASH since the program last changed

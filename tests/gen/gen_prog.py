#!/usr/bin/env python3
"""Generate the Checkpoint-3 programming cases: tests/cases/prog_*  (stdlib only, seeded, re-runnable).

Writes prog_01_tally (40,000-row transactions.csv), prog_01b_tally_small (2,000 rows), prog_02_logstat and
prog_03_solution: prompt.txt, setup/ (data + TASK.md copy of the prompt) and expect.json (run_tests.py `commands`).
Before writing anything it validates every command check: a reference implementation (kept here, never shipped)
must pass all checks, and deliberately wrong mutants/naive implementations must each fail at least one group.
Usage: python3 tests/gen/gen_prog.py
"""
import csv
import datetime
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal, ROUND_HALF_UP

# The tally prompt is HW1.md "Checkpoint 3 / Programming task" word for word, requirements 7-12 replaced.
TALLY_HEAD = """# Task: implement `tally.py`

Write a command-line tool `tally.py` (Python 3, standard library only) in the current directory.
It summarises spending stored in a CSV file. A sample data file, `transactions.csv`, is provided
(it is large: about 40,000 rows).

## Invocation

```
python3 tally.py <command> [arguments] [--file PATH] [--json]
```

* `--file PATH` selects the CSV file. Default: `transactions.csv` in the current directory.
* Options may appear anywhere after the command name.

## CSV format

The first line is a header: `date,description,category,amount`. Every following line is one transaction.
Fields follow normal CSV quoting rules (a field may be wrapped in double quotes and contain commas).
`amount` is a decimal number of dollars (it may be negative, e.g. a refund).

## Requirements

Every requirement below is checked by an automated test. Output must match exactly
(no extra lines, no extra spaces). All money values are printed with exactly two decimals
and no thousands separator, e.g. `1234.50`, `-3.00`.

1. **`count`** — `python3 tally.py count` prints the number of valid transactions, e.g. `39876`.
2. **`total`** — `python3 tally.py total` prints the sum of all amounts, e.g. `1834512.07`.
3. **`total --category NAME`** (an option of the `total` command, not a separate command) — `python3 tally.py total --category NAME` prints the sum of amounts of
   transactions whose category equals NAME. The comparison is case-insensitive.
4. **`top N`** — `python3 tally.py top N` prints the N categories with the largest total amount, one per line,
   as `CATEGORY<TAB>AMOUNT`. Sort by amount descending; if two amounts are equal, sort those categories
   alphabetically ascending. If there are fewer than N categories, print all of them.
5. **category case** — categories are case-insensitive everywhere: `Food`, `FOOD` and `food` are the
   same category. Whenever a category name is printed, it is printed in lowercase.
6. **`range --from D1 --to D2`** — `python3 tally.py range --from YYYY-MM-DD --to YYYY-MM-DD` considers transactions whose
   date is between the two dates, both ends inclusive, and prints one line `count=K total=X.XX`.

"""

TALLY_TAIL = """
When you are done, `tally.py` must be in the current directory."""

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(os.path.dirname(HERE), "cases")
Q = Decimal("0.01")


def money(d):
    s = format(d.quantize(Q, rounding=ROUND_HALF_UP), "f")
    return "0.00" if s == "-0.00" else s


def with_file(content, shell, name="t.csv"):
    """Shell snippet: write `content` to $f in a temp dir, run `shell`, keep its exit code."""
    assert content.endswith("\n") and "\nEOF\n" not in content
    return ('d=$(mktemp -d); f="$d/%s"; cat > "$f" <<\'EOF\'\n%sEOF\n%s; rc=$?; rm -rf "$d"; exit $rc'
            % (name, content, shell))


def write_case(name, prompt, setup, expect):
    d = os.path.join(CASES, name)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "setup"))
    with open(os.path.join(d, "prompt.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(prompt)
    with open(os.path.join(d, "setup", "TASK.md"), "w", encoding="utf-8", newline="") as f:
        f.write(prompt)
    for fn, txt in setup.items():
        with open(os.path.join(d, "setup", fn), "w", encoding="utf-8", newline="") as f:
            f.write(txt)
    expect = dict(expect, status="any", allow_new_files=True, timeout=300)
    with open(os.path.join(d, "expect.json"), "w", encoding="utf-8") as f:
        json.dump(expect, f, indent=1, ensure_ascii=False)
        f.write("\n")


# ----------------------------------------------------------------------------- case 1: tally

TALLY_REQS = """7. **invalid rows** — a data line is *valid* only if all of these hold (checked after the trimming of
   requirement 12): it has exactly 4 fields after CSV parsing; `date` is a real calendar date written
   `YYYY-MM-DD` (4-digit year, 2-digit month, 2-digit day); `category` is not empty; `amount` is an optional
   leading `-`, one or more digits, and optionally a `.` followed by one or two digits (so `12`, `7.5` and
   `-3.25` are valid; `abc`, `1,200.00`, `$5`, `1e3`, `.50`, `+5`, `12.345` and the empty string are not).
   Empty lines (or lines containing only spaces/tabs) are ignored. Every command silently skips invalid lines:
   they are not counted and not summed. The header line is always skipped.
8. **`--json`** — with `--json`, every command prints its result as one line of JSON, exactly as Python's
   `json.dumps(obj)` would write it (default separators, keys in the order shown), instead of the plain text.
   Money values are JSON strings formatted as above. Shapes:
   `count` -> `{"count": 5}`; `total` -> `{"total": "12.30"}`;
   `top N` -> `[{"category": "food", "total": "10.00"}, {"category": "fuel", "total": "4.50"}]` (`[]` if empty);
   `range` -> `{"count": 3, "total": "7.00"}`; `months` -> `[{"month": "2023-01", "total": "1.00"}]` (`[]` if empty);
   `average` -> `{"average": "1.23"}`.
9. **`months`** — `python3 tally.py months` prints one line per calendar month that has at least one valid
   transaction, as `YYYY-MM<TAB>AMOUNT` (the sum of that month's amounts), in ascending month order.
10. **`average [--category NAME]`** — `python3 tally.py average` prints the arithmetic mean of the amounts of all
   valid transactions; with `--category NAME` (case-insensitive) only that category's. If there are no matching
   transactions it prints `0.00`. Sums are exact (use `decimal.Decimal`, not floats) and every money value is
   rounded half away from zero to 2 decimals when printed (`Decimal.quantize(Decimal('0.01'),
   rounding=ROUND_HALF_UP)`); a value that rounds to zero is printed `0.00`, never `-0.00`.
11. **errors** — if the command is missing or unknown, an option is unknown or not allowed for that command
   (`--category` is allowed only for `total` and `average`; `--from`/`--to` only for `range`; `--file` and
   `--json` for every command; `--opt=value` is not supported), an option that needs a value is last on the
   line (the next argument is always taken as the value), a required argument is missing or extra positional
   arguments are given, `N` of `top` is not a positive integer (digits only, at least 1), or a `range` date is not
   a valid `YYYY-MM-DD` date: print one line starting with `error:` to stderr, print nothing to stdout, and
   exit with status 2. Argument errors are detected before the file is opened. If the CSV file cannot be read
   (e.g. it does not exist): print one line starting with `error:` to stderr, nothing to stdout, exit status 1.
   Successful commands exit with status 0.
12. **whitespace** — spaces and tabs at the start and end of every field are ignored (strip them after CSV
   unquoting, before validating or comparing), e.g. ` 2023-01-05 ,x, Food ,\t12.50 ` is the valid row
   `2023-01-05`, `food`, `12.50`. A category name given with `--category` is not trimmed.
"""

TALLY_PROMPT = TALLY_HEAD + TALLY_REQS + TALLY_TAIL
WS = " \t"


class TallyModel:
    """Independent in-generator model of the tally spec (used to compute expected outputs)."""

    DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
    AMT = re.compile(r"-?[0-9]+(\.[0-9]{1,2})?")

    def __init__(self, text):
        self.rows = []
        lines = text.split("\n")
        for ln in lines[1:]:
            if not ln.strip(WS):
                continue
            f = [x.strip(WS) for x in next(csv.reader([ln]))]
            if len(f) != 4 or not f[2] or not self.AMT.fullmatch(f[3]) or not self.okdate(f[0]):
                continue
            self.rows.append((f[0], f[2].lower(), Decimal(f[3])))

    @classmethod
    def okdate(cls, s):
        if not cls.DATE.fullmatch(s):
            return False
        try:
            datetime.datetime.strptime(s, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    def sel(self, cat=None):
        return [r for r in self.rows if cat is None or r[1] == cat.lower()]

    def total(self, cat=None):
        return sum((r[2] for r in self.sel(cat)), Decimal(0))

    def by(self, keyf):
        out = {}
        for r in self.rows:
            out[keyf(r)] = out.get(keyf(r), Decimal(0)) + r[2]
        return out

    def top(self, n):
        t = self.by(lambda r: r[1])
        return sorted(t.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    def rng(self, a, b):
        s = [r for r in self.rows if a <= r[0] <= b]
        return len(s), sum((r[2] for r in s), Decimal(0))

    def months(self):
        return sorted(self.by(lambda r: r[0][:7]).items())

    def average(self, cat=None):
        s = self.sel(cat)
        return sum((r[2] for r in s), Decimal(0)) / len(s) if s else Decimal(0)


def dumps(o):
    return json.dumps(o) + "\n"


def tally_commands(text, big_dates):
    """All command checks for a transactions.csv `text` (+ tiny edge-case files)."""
    M = TallyModel(text)
    C = []

    def add(group, args, out, code=0, pre="", err=None, content=None):
        cmd = "python3 tally.py " + args
        if content is not None:
            cmd = with_file(content, "python3 tally.py " + args + ' --file "$f"')
        c = {"group": group, "cmd": cmd, "stdout": out, "exit_code": code}
        if err:
            c["stderr_startswith"] = err
        C.append(c)

    def fn(cat=None):
        return M.total(cat)

    cats = [c for c, _ in M.top(100)]
    mid, small = cats[len(cats) // 2], cats[-1]
    # R1 count
    add("R1", "count", "%d\n" % len(M.rows))
    add("R1", "count --file transactions.csv", "%d\n" % len(M.rows))
    add("R1", "count", "3\n", content="date,description,category,amount\n2023-01-01,a,x,1\n2023-01-02,b,y,-2\n2023-01-03,c,x,3.5\n")
    add("R1", "count", "0\n", content="date,description,category,amount\n")
    # R2 total
    add("R2", "total", money(fn()) + "\n")
    add("R2", "total", "0.30\n", content="date,description,category,amount\n2023-01-01,a,x,0.10\n2023-01-02,b,x,0.20\n")
    add("R2", "total", "-3.00\n", content="date,description,category,amount\n2023-01-01,a,x,5.00\n2023-01-02,b,x,-8\n")
    add("R2", "total", "0.00\n", content="date,description,category,amount\n")
    # R3 total --category
    for cat in ("food", "Food", "FOOD", mid, small.upper()):
        add("R3", "total --category %s" % cat, money(fn(cat)) + "\n")
    add("R3", "total --category food --file transactions.csv", money(fn("food")) + "\n")
    add("R3", "total --file transactions.csv --category GROCERIES", money(fn("groceries")) + "\n")
    add("R3", "total --category nosuchcat", "0.00\n")
    add("R3", "total --category b", "5.00\n", content="date,description,category,amount\n2023-01-01,a,B,2.00\n2023-01-01,a,b,3.00\n2023-01-01,a,bb,9.00\n2023-01-01,a,,1.00\n")
    # R4 top N
    def toptxt(n):
        return "".join("%s\t%s\n" % (c, money(v)) for c, v in M.top(n))
    for n in (1, 3, 5, 100):
        add("R4", "top %d" % n, toptxt(n))
    add("R4", "top 2 --file transactions.csv", toptxt(2))
    tie = "date,description,category,amount\n2023-01-01,a,pear,5.00\n2023-01-01,a,apple,2.50\n2023-01-02,a,apple,2.50\n2023-01-02,a,zed,5\n2023-01-03,a,mango,7.00\n2023-01-03,a,kiwi,-1\n"
    add("R4", "top 10", "mango\t7.00\napple\t5.00\npear\t5.00\nzed\t5.00\nkiwi\t-1.00\n", content=tie)
    add("R4", "top 2", "mango\t7.00\napple\t5.00\n", content=tie)
    # R5 category case
    cs = "date,description,category,amount\n2023-01-01,a,Food,1.00\n2023-01-02,a,FOOD,2.00\n2023-01-03,a,food,3.50\n2023-01-03,a,Fuel,4.00\n2023-01-04,a,fUeL,0.25\n"
    add("R5", "top 5", "food\t6.50\nfuel\t4.25\n", content=cs)
    add("R5", "total --category FOOD", "6.50\n", content=cs)
    add("R5", "total --category fUEL", "4.25\n", content=cs)
    add("R5", "top 40", toptxt(40))
    add("R5", "top 3 --json", dumps([{"category": c, "total": money(v)} for c, v in M.top(3)]))
    # R6 range
    def rng(a, b):
        k, t = M.rng(a, b)
        return "count=%d total=%s\n" % (k, money(t))
    for a, b in (("2023-03-01", "2023-03-31"), ("2022-01-01", "2022-01-01"), ("2024-12-31", "2024-12-31"),
                 ("2024-02-29", "2024-02-29"), ("2022-06-15", "2023-09-20"), ("2000-01-01", "2030-12-31"),
                 ("2025-01-01", "2026-01-01"), ("2023-05-10", "2023-05-09")):
        add("R6", "range --from %s --to %s" % (a, b), rng(a, b))
    add("R6", "range --to 2023-02-28 --from 2023-02-01 --file transactions.csv", rng("2023-02-01", "2023-02-28"))
    rg = "date,description,category,amount\n2023-01-01,a,x,1.00\n2023-01-15,b,x,2.00\n2023-01-31,c,y,4.00\n2023-02-01,d,y,8.00\n"
    add("R6", "range --from 2023-01-01 --to 2023-01-31", "count=3 total=7.00\n", content=rg)
    add("R6", "range --from 2023-01-02 --to 2023-01-30", "count=1 total=2.00\n", content=rg)
    add("R6", "range --from 2023-01-31 --to 2023-02-01", "count=2 total=12.00\n", content=rg)
    # R7 invalid rows
    dirty = ("date,description,category,amount\n"
             "2023-01-01,ok,x,1.00\n"          # valid
             "2023-01-02,three,x\n"            # 3 fields
             "2023-01-03,five,x,1,000\n"       # 5 fields
             "2023-01-04,bad,x,abc\n"
             '2023-01-05,bad,x,"1,200.00"\n'
             "2023-01-06,bad,x,$5\n"
             "2023-01-07,bad,x,1e3\n"
             "2023-01-08,bad,x,.50\n"
             "2023-01-09,bad,x,+5\n"
             "2023-01-10,bad,x,12.345\n"
             "2023-01-11,bad,x,\n"
             "2023-02-30,bad,x,9.00\n"
             "2023-13-01,bad,x,9.00\n"
             "2023-1-5,bad,x,9.00\n"
             "01/05/2023,bad,x,9.00\n"
             ",bad,x,9.00\n"
             "2023-01-12,bad,,9.00\n"
             "2023-01-13,bad,   ,9.00\n"
             "\n"
             "   \n"
             "2023-01-14,ok,x,7.5\n"
             "2024-02-29,leap,y,-3\n"          # valid leap day
             "2023-02-29,notleap,y,9.00\n"
             "2023-01-15,ok,Y,2.25\n")
    dm = TallyModel(dirty)
    assert len(dm.rows) == 4 and dm.total() == Decimal("7.75"), (len(dm.rows), dm.total())
    add("R7", "count", "4\n", content=dirty)
    add("R7", "total", "7.75\n", content=dirty)
    add("R7", "top 5", "x\t8.50\ny\t-0.75\n", content=dirty)
    add("R7", "range --from 2023-01-01 --to 2024-12-31", "count=4 total=7.75\n", content=dirty)
    add("R7", "months", "2023-01\t10.75\n2024-02\t-3.00\n", content=dirty)
    add("R7", "average --category x", "4.25\n", content=dirty)
    add("R7", "count", "0\n", content="date,description,category,amount\n2023-01-02,a,x\n\nfoo\n")
    # R8 --json
    add("R8", "count --json", dumps({"count": len(M.rows)}))
    add("R8", "total --json", dumps({"total": money(fn())}))
    add("R8", "total --json --category Food", dumps({"total": money(fn("food"))}))
    add("R8", "top 4 --json", dumps([{"category": c, "total": money(v)} for c, v in M.top(4)]))
    k, t = M.rng("2023-03-01", "2023-03-31")
    add("R8", "range --json --from 2023-03-01 --to 2023-03-31", dumps({"count": k, "total": money(t)}))
    add("R8", "months --json", dumps([{"month": m, "total": money(v)} for m, v in M.months()]))
    add("R8", "average --json", dumps({"average": money(M.average())}))
    add("R8", "average --category fuel --json --file transactions.csv", dumps({"average": money(M.average("fuel"))}))
    add("R8", "top 3 --json", '[]\n', content="date,description,category,amount\n")
    add("R8", "months --json", '[]\n', content="date,description,category,amount\n2023-01-01,a,,1\n")
    add("R8", "total --json", '{"total": "0.00"}\n', content="date,description,category,amount\n")
    # R9 months
    add("R9", "months", "".join("%s\t%s\n" % (m, money(v)) for m, v in M.months()))
    add("R9", "months --file transactions.csv", "".join("%s\t%s\n" % (m, money(v)) for m, v in M.months()))
    mo = "date,description,category,amount\n2023-03-31,a,x,1.10\n2022-12-01,a,x,2.20\n2023-03-01,a,y,3.30\n2023-01-15,a,y,-9\n"
    add("R9", "months", "2022-12\t2.20\n2023-01\t-9.00\n2023-03\t4.40\n", content=mo)
    add("R9", "months", "", content="date,description,category,amount\n")
    # R10 average
    add("R10", "average", money(M.average()) + "\n")
    for cat in ("food", "TRAVEL", mid, "nosuchcat"):
        add("R10", "average --category %s" % cat, money(M.average(cat)) + "\n")
    av = "date,description,category,amount\n2023-01-01,a,x,0.01\n2023-01-01,a,x,0.00\n2023-01-01,a,y,-0.01\n2023-01-01,a,y,0\n2023-01-01,a,z,2.50\n2023-01-01,a,z,0.01\n2023-01-01,a,w,1\n2023-01-01,a,w,0.01\n2023-01-01,a,w,0.01\n"
    add("R10", "average --category x", "0.01\n", content=av)       # 0.005 -> 0.01
    add("R10", "average --category y", "-0.01\n", content=av)      # -0.005 -> -0.01 (away from zero)
    add("R10", "average --category z", "1.26\n", content=av)       # 1.255 -> 1.26
    add("R10", "average --category w", "0.34\n", content=av)       # 1.02/3 = 0.34
    add("R10", "average", "0.39\n", content=av)                    # 3.53/9 = 0.392
    add("R10", "average --category none", "0.00\n", content=av)
    add("R10", "average --category q", "0.00\n", content="date,description,category,amount\n2023-01-01,a,q,0.001\n2023-01-01,a,r,5\n")
    add("R10", "average --category q", "0.00\n", content="date,description,category,amount\n2023-01-01,a,q,-0.01\n2023-01-01,a,q,0.01\n")
    add("R10", "average --category r", "0.00\n", content="date,description,category,amount\n2023-01-01,a,r,-0.01\n2023-01-01,a,r,0\n2023-01-01,a,r,0\n")  # -0.00333 -> 0.00 not -0.00
    # R11 errors
    def err(args, code=2, content=None):
        add("R11", args, "", code=code, err="error:", content=content)
    for a in ("", "bogus", "top", "top abc", "top 0", "top -1", "top 3.0", "top +3", "top 3 4", "count extra",
              "range --from 2023-01-01", "range --to 2023-01-01", "range --from 2023-02-30 --to 2023-03-01",
              "range --from 2023-01-01 --to 01/02/2023", "total --category", "count --bogus", "count --category food",
              "months --from 2023-01-01", "top 3 --category food", "total --file", "total --json --file",
              "total --category=food", "range --from 2023-01-01 --to 2023-01-02 extra", "average --from 2023-01-01",
              "--json count"):
        err(a)
    err("count --file /nonexistent/dir/none.csv", 1)
    err("total --file /nonexistent/dir/none.csv --json", 1)
    err("bogus --file /nonexistent/dir/none.csv", 2)     # argument errors win over file errors
    err("top x --file /nonexistent/dir/none.csv", 2)
    # R12 whitespace
    ws = ("date,description,category,amount\n"
          " 2023-01-05 ,x, Food ,\t12.50 \n"
          '\t2023-01-06,"a, b","  FOOD",  3\n'
          '2023-01-07 ,y,"\tfood ", -1.25 \n'
          "2023-01-08,z,  ,5\n"
          "2023-01-09,z,food,  \n"
          "  2023-01-10  ,z,drink,\t2\t\n"
          "2023-02-30 ,z,drink,2\n")
    wm = TallyModel(ws)
    assert len(wm.rows) == 4
    add("R12", "count", "4\n", content=ws)
    add("R12", "total", "16.25\n", content=ws)
    add("R12", "top 5", "food\t14.25\ndrink\t2.00\n", content=ws)
    add("R12", "total --category food", "14.25\n", content=ws)
    add("R12", "range --from 2023-01-05 --to 2023-01-06", "count=2 total=15.50\n", content=ws)
    add("R12", "months --json", '[{"month": "2023-01", "total": "16.25"}]\n', content=ws)
    add("R12", "average --category drink", "2.00\n", content=ws)
    add("R12", "count", str(len(M.rows)) + "\n")
    return C


def gen_tally(n, seed):
    """Return (csv_text, stats). ~n data lines incl. dirty ones and a few blank lines."""
    rnd = random.Random(seed)
    cats = [("food", 26), ("groceries", 20), ("rent", 3), ("fuel", 9), ("travel", 7), ("utilities", 8),
            ("entertainment", 8), ("health", 5), ("clothing", 5), ("education", 3), ("gifts", 3),
            ("dining out", 7), ("subscriptions", 6), ("insurance", 2), ("charity", 2)]
    names, wts = zip(*cats)
    descs = ["Coffee", "Lunch, downtown", "Weekly shop", "Gas station", "Bus pass", "Electric bill", "Movie night",
             "Pharmacy", 'Book "Dune"', "Taxi, airport", "Gift, birthday", "Monthly plan", "Cafe Zoe", "Refund",
             "Online order", "Parking", "Dinner, 4 people", "Streaming", "Donation", ""]
    d0 = datetime.date(2022, 1, 1)
    span = (datetime.date(2024, 12, 31) - d0).days
    out = ["date,description,category,amount"]
    st = {"valid": 0, "dirty": 0, "blank": 0}

    def row(fields):
        b = io.StringIO()
        csv.writer(b, lineterminator="").writerow(fields)
        return b.getvalue()

    def variant(c):
        r = rnd.random()
        return c if r < .55 else c.title() if r < .8 else c.upper()

    def amount():
        c = max(1, int(rnd.lognormvariate(3.3, 1.0) * 100) % 200000)
        neg = rnd.random() < .04
        s = "%d.%02d" % divmod(c, 100)
        r = rnd.random()
        if c % 100 == 0 and r < .5:
            s = "%d" % (c // 100)
        elif c % 10 == 0 and r < .5:
            s = "%d.%d" % divmod(c // 10, 10)
        return ("-" + s) if neg else s

    def dirty():
        k = rnd.randrange(5)
        dt = (d0 + datetime.timedelta(rnd.randrange(span))).isoformat()
        base = [dt, rnd.choice(descs), variant(rnd.choice(names)), amount()]
        if k == 0:
            return row(base[:rnd.choice((1, 2, 3))])
        if k == 1:
            return row(base) + "," + rnd.choice(["x", "", "7"]) if rnd.random() < .5 else dt + ",Big buy,food,1,200.50"
        if k == 2:
            base[3] = rnd.choice(["abc", "", "N/A", "$12.50", "12.345", "1e3", "1,200.00", "--5", "5-", "twelve", ".75", "+4"])
        elif k == 3:
            base[0] = rnd.choice(["2023-02-30", "2023-13-05", "05/04/2023", "2023-4-5", "2023-00-10", "yesterday", "",
                                  "2022-02-29", "2023-06-31", "23-06-01", "2023/06/01"])
        else:
            base[2] = rnd.choice(["", "   ", "\t"])
        return row(base)

    for i in range(n):
        r = rnd.random()
        if i == 5 or i == n - 5:  # boundary dates
            dt = "2022-01-01" if i == 5 else "2024-12-31"
        else:
            dt = (d0 + datetime.timedelta(rnd.randrange(span + 1))).isoformat()
        if r < .011 and i not in (5, n - 5):
            out.append(dirty())
            st["dirty"] += 1
            continue
        f = [dt, rnd.choice(descs), variant(rnd.choices(names, wts)[0]), amount()]
        if r > .985:  # padded
            p = lambda s: rnd.choice(["", " ", "  ", "\t"]) + s + rnd.choice(["", " ", "\t", "  "])
            f = [p(f[0]), f[1], p(f[2]), p(f[3])]
            ln = row(f)
        elif r > .975:  # quoted category / amount with padding inside quotes
            ln = "%s,%s,\"%s\",\"%s\"" % (f[0], row([f[1]]), " " + f[2] + " ", f[3])
        else:
            ln = row(f)
        out.append(ln)
        st["valid"] += 1
        if rnd.random() < .0003:
            out.append(rnd.choice(["", "   "]))
            st["blank"] += 1
    return "\n".join(out) + "\n", st


# Reference + mutant implementations (used only to validate the checks; never shipped in setup/).
REF_TALLY = r'''
import sys, csv, json, re, datetime
from decimal import Decimal, ROUND_HALF_UP

def die(msg, code=2):
    sys.stderr.write("error: %s\n" % msg)
    sys.exit(code)

def money(d):
    s = format(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
    return "0.00" if s == "-0.00" else s

def okdate(s):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", s):
        return False
    try:
        datetime.date(int(s[:4]), int(s[5:7]), int(s[8:]))
        return True
    except ValueError:
        return False

def load(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            lines = f.read().split("\n")
    except (OSError, UnicodeDecodeError) as e:
        die("cannot read %s: %s" % (path, e), 1)
    rows = []
    for ln in lines[1:]:
        if not ln.strip(" \t"):
            continue
        fl = [x.strip(" \t") for x in next(csv.reader([ln]))]
        if len(fl) != 4 or not okdate(fl[0]) or not fl[2] or not re.fullmatch(r"-?[0-9]+(\.[0-9]{1,2})?", fl[3]):
            continue
        rows.append((fl[0], fl[2].lower(), Decimal(fl[3])))
    return rows

def main(argv):
    if not argv:
        die("missing command")
    cmd, rest = argv[0], argv[1:]
    if cmd not in ("count", "total", "top", "range", "months", "average"):
        die("unknown command %s" % cmd)
    valued = {"--file", "--from", "--to"}
    if cmd in ("total", "average"):
        valued.add("--category")
    if cmd != "range":
        valued -= {"--from", "--to"}
    o, pos, js, i = {}, [], False, 0
    while i < len(rest):
        a = rest[i]
        if a == "--json":
            js = True
        elif a in valued:
            if i + 1 >= len(rest):
                die("option %s needs a value" % a)
            o[a] = rest[i + 1]
            i += 1
        elif a.startswith("--"):
            die("unknown option %s" % a)
        else:
            pos.append(a)
        i += 1
    need = 1 if cmd == "top" else 0
    if len(pos) != need:
        die("wrong number of arguments")
    if cmd == "top":
        if not re.fullmatch(r"[0-9]+", pos[0]) or int(pos[0]) < 1:
            die("N must be a positive integer")
    if cmd == "range":
        if "--from" not in o or "--to" not in o or not okdate(o["--from"]) or not okdate(o["--to"]):
            die("range needs valid --from and --to")
    rows = load(o.get("--file", "transactions.csv"))
    z = Decimal(0)
    if cmd == "count":
        r, t = len(rows), "%d" % len(rows)
        r = {"count": len(rows)}
    elif cmd == "total":
        sel = [x for x in rows if "--category" not in o or x[1] == o["--category"].lower()]
        v = money(sum((x[2] for x in sel), z)); r, t = {"total": v}, v
    elif cmd == "average":
        sel = [x for x in rows if "--category" not in o or x[1] == o["--category"].lower()]
        v = money(sum((x[2] for x in sel), z) / len(sel)) if sel else "0.00"; r, t = {"average": v}, v
    elif cmd == "range":
        sel = [x for x in rows if o["--from"] <= x[0] <= o["--to"]]
        v = money(sum((x[2] for x in sel), z)); r, t = {"count": len(sel), "total": v}, "count=%d total=%s" % (len(sel), v)
    else:
        agg = {}
        for x in rows:
            k = x[1] if cmd == "top" else x[0][:7]
            agg[k] = agg.get(k, z) + x[2]
        if cmd == "top":
            items = sorted(agg.items(), key=lambda kv: (-kv[1], kv[0]))[:int(pos[0])]
            key = "category"
        else:
            items = sorted(agg.items()); key = "month"
        r = [{key: k, "total": money(v)} for k, v in items]
        t = "\n".join("%s\t%s" % (k, money(v)) for k, v in items)
    if js:
        print(json.dumps(r))
    elif t != "":
        print(t)

main(sys.argv[1:])
'''

# float / case-sensitive / no validation / traceback-on-error implementation
NAIVE_TALLY = r'''
import sys, csv, json
args = sys.argv[1:]
def opt(n):
    return args[args.index(n) + 1] if n in args else None
rows = []
for r in list(csv.reader(open(opt("--file") or "transactions.csv")))[1:]:
    try:
        rows.append((r[0], r[2], float(r[3])))
    except Exception:
        pass
cmd = args[0]
if cmd == "count":
    print(len(rows))
elif cmd == "total":
    c = opt("--category")
    print("%.2f" % sum(a for d, k, a in rows if c is None or k == c))
elif cmd == "average":
    c = opt("--category")
    s = [a for d, k, a in rows if c is None or k == c]
    print("%.2f" % (sum(s) / len(s)) if s else "0.00")
elif cmd == "top":
    t = {}
    for d, k, a in rows:
        t[k] = t.get(k, 0) + a
    for k, v in sorted(t.items(), key=lambda kv: -kv[1])[:int(args[1])]:
        print("%s\t%.2f" % (k, v))
elif cmd == "range":
    s = [a for d, k, a in rows if opt("--from") <= d <= opt("--to")]
    print("count=%d total=%.2f" % (len(s), sum(s)))
elif cmd == "months":
    t = {}
    for d, k, a in rows:
        t[d[:7]] = t.get(d[:7], 0) + a
    for k in sorted(t):
        print("%s\t%.2f" % (k, t[k]))
'''


def mutant(src, *pairs):
    for a, b in pairs:
        assert a in src, a
        src = src.replace(a, b)
    return src


TALLY_MUTANTS = {
    "half_even": mutant(REF_TALLY, ("ROUND_HALF_UP", "ROUND_HALF_EVEN")),
    "no_calendar_check": mutant(REF_TALLY, ("        datetime.date(int(s[:4]), int(s[5:7]), int(s[8:]))\n", "        pass\n")),
    "no_strip": mutant(REF_TALLY, ('x.strip(" \\t") for x in', "x for x in")),
    "case_sensitive": mutant(REF_TALLY, (".lower()", "")),
    "exit1_for_usage": mutant(REF_TALLY, ("def die(msg, code=2):", "def die(msg, code=2):\n    code = 1")),
    "negzero": mutant(REF_TALLY, ('return "0.00" if s == "-0.00" else s', "return s")),
}


# ----------------------------------------------------------------------------- case 2: logstat

LOGSTAT_PROMPT = """# Task: implement `logstat.py`

Write a command-line tool `logstat.py` (Python 3, standard library only) in the current directory.
It summarises a web-server access log. A sample log, `access.log`, is provided
(it is large: about 20,000 lines).

## Invocation

```
python3 logstat.py <command> [arguments] [--file PATH] [--since TS] [--method M]
```

* `--file PATH` selects the log file. Default: `access.log` in the current directory.
* Options may appear anywhere after the command name. An option that takes a value always uses the next
  argument as its value; `--opt=value` is not supported.

## Log format

Each line is one request in this form (whitespace-separated, single spaces), all of it required:

```
IP IDENT USER [DD/Mon/YYYY:HH:MM:SS +ZZZZ] "METHOD /target HTTP/V.V" STATUS BYTES
```

e.g. `203.0.113.7 - frank [10/Mar/2024:13:55:36 -0500] "GET /index.html?x=1 HTTP/1.1" 200 2326`

* `IP`, `IDENT`, `USER` are any non-empty tokens without spaces (`-` is common).
* `Mon` is exactly one of `Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec`; the date and time must be real
  (e.g. not 31 Feb; hour 00-23, minute and second 00-59).
* `+ZZZZ` is a sign followed by 4 digits `HHMM` (HH 00-23, MM 00-59): the offset of the local time from UTC.
  The UTC instant of a line is its local time minus that offset.
* `METHOD` is one or more uppercase ASCII letters; `/target` is a token without spaces that starts with `/`;
  `V.V` is one digit, a dot, one digit.
* `STATUS` is 3 digits with a value from 100 to 599. `BYTES` is a non-negative integer, or `-` which counts as 0.
* The *path* of a request is its `/target` up to (not including) the first `?`.

## Requirements

Every requirement below is checked by an automated test. Output must match exactly (no extra lines, no extra
spaces); integers are printed in plain decimal.

1. **`count`** — prints the number of valid requests.
2. **`status`** — prints one line `STATUS<TAB>COUNT` for every status code that occurs, in ascending
   numeric order of the code.
3. **`top N`** — prints the N most requested paths, one per line, as `PATH<TAB>COUNT`. Sort by count
   descending; ties by path ascending (plain string comparison). If there are fewer than N paths, print all
   of them. `N` must be a positive integer (digits only, at least 1).
4. **`bytes`** — prints the sum of BYTES over all valid requests.
5. **`errors`** — prints one line `client=K server=K`: the number of requests with status 400-499 and with
   status 500-599.
6. **`ips`** — prints the number of distinct `IP` values (exact string comparison).
7. **`--since TS`** — `TS` has the form `YYYY-MM-DDTHH:MM:SS` (a real date and time, interpreted as UTC). Every
   command then considers only requests whose UTC instant is at or after `TS`.
8. **`--method M`** — every command then considers only requests whose method equals `M`, compared
   case-insensitively (`--method get` matches `GET`). `M` must consist of ASCII letters only. `--since` and
   `--method` may be combined.
9. **invalid lines** — a line is valid only if it follows the format above exactly (surrounding whitespace on
   the line is ignored). Invalid lines and empty lines are silently skipped by every command.
10. **errors and exit codes** — a missing or unknown command, an unknown option, an option without a value,
   a missing or extra positional argument, an invalid `N`, `--since` or `--method` value: print one line
   starting with `error:` to stderr, nothing to stdout, exit with status 2 (argument errors are detected before
   the file is opened). If the log file cannot be read (e.g. it does not exist): one line starting with
   `error:` to stderr, nothing to stdout, exit status 1. Successful commands exit with status 0.

When you are done, `logstat.py` must be in the current directory.
"""

LOGSTAT_RE = (r'(\S+) (\S+) (\S+) \[(\d\d)/([A-Za-z]{3})/(\d{4}):(\d\d):(\d\d):(\d\d) ([+-])(\d\d)(\d\d)\]'
              r' "([A-Z]+) (/\S*) HTTP/\d\.\d" (\d{3}) (\d+|-)')
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


class LogModel:
    def __init__(self, text, since=None, method=None):
        self.reqs = []
        pat = re.compile(LOGSTAT_RE)
        for ln in text.split("\n"):
            m = pat.fullmatch(ln.strip())
            if not m:
                continue
            g = m.groups()
            if g[4] not in MONTHS or not (100 <= int(g[14]) <= 599) or int(g[10]) > 23 or int(g[11]) > 59:
                continue
            try:
                loc = datetime.datetime(int(g[5]), MONTHS.index(g[4]) + 1, int(g[3]), int(g[6]), int(g[7]), int(g[8]))
            except ValueError:
                continue
            off = datetime.timedelta(hours=int(g[10]), minutes=int(g[11])) * (1 if g[9] == "+" else -1)
            utc = loc - off
            if since and utc < since:
                continue
            if method and g[12] != method.upper():
                continue
            self.reqs.append(dict(ip=g[0], utc=utc, method=g[12], path=g[13].split("?")[0], st=int(g[14]),
                                  by=0 if g[15] == "-" else int(g[15])))

    def count(self):
        return "%d\n" % len(self.reqs)

    def status(self):
        c = {}
        for r in self.reqs:
            c[r["st"]] = c.get(r["st"], 0) + 1
        return "".join("%d\t%d\n" % kv for kv in sorted(c.items()))

    def top(self, n):
        c = {}
        for r in self.reqs:
            c[r["path"]] = c.get(r["path"], 0) + 1
        return "".join("%s\t%d\n" % kv for kv in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:n])

    def bytes(self):
        return "%d\n" % sum(r["by"] for r in self.reqs)

    def errors(self):
        return "client=%d server=%d\n" % (sum(400 <= r["st"] < 500 for r in self.reqs), sum(r["st"] >= 500 for r in self.reqs))

    def ips(self):
        return "%d\n" % len({r["ip"] for r in self.reqs})


def logstat_commands(text):
    C = []
    since_all = LogModel(text).reqs

    def add(group, args, out, code=0, err=None, content=None):
        cmd = "python3 logstat.py " + args
        if content is not None:
            cmd = with_file(content, "python3 logstat.py " + args + ' --file "$f"', "t.log")
        c = {"group": group, "cmd": cmd, "stdout": out, "exit_code": code}
        if err:
            c["stderr_startswith"] = err
        C.append(c)

    add("R1", "count", LogModel(text).count())
    add("R1", "count --file access.log", LogModel(text).count())
    add("R2", "status", LogModel(text).status())
    add("R2", "status --file access.log", LogModel(text).status())
    for n in (1, 3, 10, 5000):
        add("R3", "top %d" % n, LogModel(text).top(n))
    add("R4", "bytes", LogModel(text).bytes())
    add("R5", "errors", LogModel(text).errors())
    add("R6", "ips", LogModel(text).ips())
    # R7 --since (also with timezone conversion)
    utcs = sorted(r["utc"] for r in since_all)
    pivot = utcs[len(utcs) // 3]
    for ts in (pivot, datetime.datetime(2024, 3, 8), utcs[-1], utcs[-1] + datetime.timedelta(seconds=1), datetime.datetime(2020, 1, 1)):
        s = ts.strftime("%Y-%m-%dT%H:%M:%S")
        M = LogModel(text, ts)
        outs = [("count", M.count()), ("status", M.status())]
        if ts == pivot:
            outs += [("top 3", M.top(3)), ("ips", M.ips()), ("errors", M.errors())]
        for name, o in outs:
            add("R7", "%s --since %s" % (name, s), o)
    add("R7", "bytes --since %s --file access.log" % pivot.strftime("%Y-%m-%dT%H:%M:%S"), LogModel(text, pivot).bytes())
    tz = ('1.1.1.1 - - [10/Mar/2024:23:30:00 -0500] "GET /a HTTP/1.1" 200 10\n'      # 2024-03-11T04:30:00Z
          '1.1.1.2 - - [11/Mar/2024:05:00:00 +0530] "GET /b HTTP/1.1" 200 20\n'      # 2024-03-10T23:30:00Z
          '1.1.1.3 - - [11/Mar/2024:04:30:00 +0000] "GET /c HTTP/1.1" 200 30\n'      # 2024-03-11T04:30:00Z
          '1.1.1.4 - - [11/Mar/2024:06:30:00 +0200] "GET /d HTTP/1.1" 200 40\n')     # 2024-03-11T04:30:00Z
    add("R7", "count --since 2024-03-11T04:30:00", "3\n", content=tz)
    add("R7", "bytes --since 2024-03-11T04:30:00", "80\n", content=tz)
    add("R7", "count --since 2024-03-11T04:30:01", "0\n", content=tz)
    add("R7", "count --since 2024-03-10T23:30:00", "4\n", content=tz)
    add("R7", "top 5 --since 2024-03-11T04:30:00", "/a\t1\n/c\t1\n/d\t1\n", content=tz)
    # R8 --method
    for m in ("GET", "post", "Delete", "HEAD", "PUT", "OPTIONS"):
        M = LogModel(text, None, m)
        add("R8", "count --method %s" % m, M.count())
    for m in ("POST", "get"):
        M = LogModel(text, None, m)
        add("R8", "status --method %s" % m, M.status())
        add("R8", "top 4 --method %s" % m, M.top(4))
    ts = pivot
    M = LogModel(text, ts, "post")
    s = ts.strftime("%Y-%m-%dT%H:%M:%S")
    add("R8", "bytes --method post --since %s" % s, M.bytes())
    add("R8", "errors --since %s --method PUT" % s, LogModel(text, ts, "PUT").errors())
    add("R8", "ips --method GET", LogModel(text, None, "GET").ips())
    # R9 dirty lines
    dirty = ('10.0.0.1 - - [05/Mar/2024:10:00:00 +0000] "GET /ok HTTP/1.1" 200 100\n'
             '10.0.0.2 - bob [05/Mar/2024:10:00:01 +0000] "POST /ok?a=1&b=2 HTTP/1.0" 404 -\n'
             '  10.0.0.3 - - [05/Mar/2024:10:00:02 +0000] "GET /ok HTTP/1.1" 500 7  \n'
             '\n'
             '   \n'
             'garbage line\n'
             '10.0.0.4 - - [31/Feb/2024:10:00:00 +0000] "GET /bad HTTP/1.1" 200 1\n'
             '10.0.0.4 - - [05/Foo/2024:10:00:00 +0000] "GET /bad HTTP/1.1" 200 1\n'
             '10.0.0.4 - - [05/mar/2024:10:00:00 +0000] "GET /bad HTTP/1.1" 200 1\n'
             '10.0.0.4 - - [05/Mar/2024:25:00:00 +0000] "GET /bad HTTP/1.1" 200 1\n'
             '10.0.0.4 - - [05/Mar/2024:10:61:00 +0000] "GET /bad HTTP/1.1" 200 1\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 +2460] "GET /bad HTTP/1.1" 200 1\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 0000] "GET /bad HTTP/1.1" 200 1\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 +0000] "get /bad HTTP/1.1" 200 1\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 +0000] "GET bad HTTP/1.1" 200 1\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 +0000] "GET /bad HTTP/1.1" 099 1\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 +0000] "GET /bad HTTP/1.1" 600 1\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 +0000] "GET /bad HTTP/1.1" 20 1\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 +0000] "GET /bad HTTP/1.1" 200 -5\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 +0000] "GET /bad HTTP/1.1" 200\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 +0000] "GET /bad HTTP/11" 200 1\n'
             '10.0.0.4 - - [05/Mar/2024:10:00:00 +0000] "GET /bad" 200 1\n'
             '10.0.0.4 - - 05/Mar/2024:10:00:00 +0000 "GET /bad HTTP/1.1" 200 1\n'
             '10.0.0.5 - - [29/Feb/2024:23:59:59 -1100] "HEAD /leap HTTP/1.1" 301 0\n'
             '10.0.0.5 - - [29/Feb/2023:23:59:59 -1100] "HEAD /noleap HTTP/1.1" 301 0\n'
             '2001:db8::1 - - [05/Mar/2024:10:00:03 +0000] "GET /ok HTTP/2.0" 503 5\n')
    dm = LogModel(dirty)
    assert len(dm.reqs) == 5, len(dm.reqs)
    add("R9", "count", "5\n", content=dirty)
    add("R9", "status", "200\t1\n301\t1\n404\t1\n500\t1\n503\t1\n", content=dirty)
    add("R9", "top 5", "/ok\t4\n/leap\t1\n", content=dirty)
    add("R9", "bytes", "112\n", content=dirty)
    add("R9", "errors", "client=1 server=2\n", content=dirty)
    add("R9", "ips", "5\n", content=dirty)
    add("R9", "count", "0\n", content="\n\nnothing valid here\n")
    add("R9", "bytes", "0\n", content="\n")
    # R10 errors
    def err(args, code=2):
        add("R10", args, "", code=code, err="error:")
    for a in ("", "bogus", "top", "top x", "top 0", "top -2", "top 2.5", "top 1 2", "count extra", "count --bogus",
              "count --since", "count --since 2024-03-10", "count --since 2024-03-10 10:00:00", "count --since 2024-02-30T00:00:00",
              "count --since 2024-03-10T24:00:00", "count --method", "count --method G3T", "count --method ''", "count --file",
              "count --since=2024-03-10T00:00:00", "ips extra", "--method get count", "status --json"):
        err(a)
    err("count --file /nonexistent/dir/x.log", 1)
    err("top 3 --file /nonexistent/dir/x.log --method get", 1)
    err("bogus --file /nonexistent/dir/x.log", 2)
    err("count --since nope --file /nonexistent/dir/x.log", 2)
    add("R10", "count --file access.log", LogModel(text).count())
    return C


def gen_log(n, seed):
    rnd = random.Random(seed)
    paths = ["/", "/index.html", "/login", "/logout", "/api/users", "/api/orders", "/api/orders/42", "/static/app.js",
             "/static/app.css", "/static/logo.png", "/search", "/cart", "/checkout", "/about", "/contact", "/api/health",
             "/products", "/products/17", "/products/18", "/favicon.ico", "/robots.txt", "/admin", "/wp-login.php",
             "/api/v2/items", "/blog/2024/03/hello-world", "/blog/2024/02/release-notes", "/img/a%20b.png", "/a/b/c/d/e"]
    wts = [30, 14, 10, 4, 12, 9, 2, 14, 10, 8, 7, 5, 3, 3, 2, 6, 6, 3, 3, 9, 4, 2, 3, 5, 2, 1, 1, 1]
    tzs = [("+0000", 0), ("-0500", -300), ("+0530", 330), ("+0200", 120)]
    tzw = [70, 15, 10, 5]
    ips = ["198.51.100.%d" % i for i in range(1, 60)] + ["203.0.113.%d" % i for i in range(1, 90)] + \
          ["2001:db8::%x" % i for i in range(1, 12)]
    users = ["-"] * 12 + ["frank", "alice", "bob"]
    methods = ["GET"] * 70 + ["POST"] * 18 + ["PUT"] * 4 + ["DELETE"] * 3 + ["HEAD"] * 4 + ["OPTIONS"]
    sts = [200] * 60 + [304] * 8 + [301] * 4 + [302] * 3 + [404] * 12 + [403] * 3 + [401] * 2 + [500] * 4 + [502] * 2 + [503] * 2 + [429]
    t0 = datetime.datetime(2024, 3, 1)
    lines = []
    for i in range(n):
        utc = t0 + datetime.timedelta(seconds=rnd.randrange(14 * 86400))
        tz, mins = rnd.choices(tzs, tzw)[0]
        loc = utc + datetime.timedelta(minutes=mins)
        ts = "%02d/%s/%d:%s %s" % (loc.day, MONTHS[loc.month - 1], loc.year, loc.strftime("%H:%M:%S"), tz)
        tgt = rnd.choices(paths, wts)[0]
        if rnd.random() < .15:
            tgt += rnd.choice(["?page=2", "?q=shoes&sort=asc", "?utm=mail", "?"])
        by = rnd.choice(["-", str(rnd.randrange(0, 3000000)), str(rnd.randrange(0, 5000))]) if rnd.random() < .97 else "0"
        ln = '%s - %s [%s] "%s %s HTTP/%s" %d %s' % (rnd.choice(ips), rnd.choice(users), ts, rnd.choice(methods), tgt,
                                                    rnd.choice(["1.1", "1.1", "1.0", "2.0"]), rnd.choice(sts), by)
        r = rnd.random()
        if r < .012:  # corrupt
            k = rnd.randrange(8)
            ln = [ln[:rnd.randrange(10, 60)],
                  ln.replace("/Mar/", "/Foo/"),
                  ln.replace('" ', '" x '),
                  re.sub(r'"([A-Z]+) ', lambda m: '"' + m.group(1).lower() + ' ', ln),
                  ln.rsplit(" ", 2)[0] + " 999 " + ln.rsplit(" ", 1)[1],
                  re.sub(r"\[[^\]]*\]", "[31/Feb/2024:10:00:00 +0000]", ln),
                  re.sub(r"\] \"(\S+) (\S+)", r'] "\1 x\2', ln),
                  "# " + ln][k]
        elif r < .016:
            ln = rnd.choice(["", "  ", "\t"])
        elif r < .02:
            ln = "  " + ln + rnd.choice(["", " ", "  "])
        lines.append(ln)
    return "\n".join(lines) + "\n"


REF_LOGSTAT = r'''
import sys, re, datetime
MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
PAT = re.compile(r'(\S+) (\S+) (\S+) \[(\d\d)/([A-Za-z]{3})/(\d{4}):(\d\d):(\d\d):(\d\d) ([+-])(\d\d)(\d\d)\] "([A-Z]+) (/\S*) HTTP/\d\.\d" (\d{3}) (\d+|-)')

def die(msg, code=2):
    sys.stderr.write("error: %s\n" % msg)
    sys.exit(code)

def parse(ln):
    m = PAT.fullmatch(ln.strip())
    if not m:
        return None
    ip, mon, st, by = m.group(1), m.group(5), int(m.group(15)), m.group(16)
    if mon not in MON or not 100 <= st <= 599 or int(m.group(11)) > 23 or int(m.group(12)) > 59:
        return None
    try:
        t = datetime.datetime(int(m.group(6)), MON.index(mon) + 1, int(m.group(4)), int(m.group(7)), int(m.group(8)), int(m.group(9)))
    except ValueError:
        return None
    off = datetime.timedelta(hours=int(m.group(11)), minutes=int(m.group(12)))
    t = t - off if m.group(10) == "+" else t + off
    return ip, t, m.group(13), m.group(14).split("?")[0], st, 0 if by == "-" else int(by)

def main(argv):
    if not argv:
        die("missing command")
    cmd, rest = argv[0], argv[1:]
    if cmd not in ("count", "status", "top", "bytes", "errors", "ips"):
        die("unknown command")
    o, pos, i = {}, [], 0
    while i < len(rest):
        a = rest[i]
        if a in ("--file", "--since", "--method"):
            if i + 1 >= len(rest):
                die("missing value for " + a)
            o[a] = rest[i + 1]
            i += 1
        elif a.startswith("--"):
            die("unknown option " + a)
        else:
            pos.append(a)
        i += 1
    if len(pos) != (1 if cmd == "top" else 0):
        die("wrong number of arguments")
    if cmd == "top" and (not re.fullmatch(r"[0-9]+", pos[0]) or int(pos[0]) < 1):
        die("bad N")
    since = None
    if "--since" in o:
        if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", o["--since"]):
            die("bad --since")
        try:
            since = datetime.datetime.strptime(o["--since"], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            die("bad --since")
    meth = None
    if "--method" in o:
        if not re.fullmatch(r"[A-Za-z]+", o["--method"]):
            die("bad --method")
        meth = o["--method"].upper()
    try:
        with open(o.get("--file", "access.log"), encoding="utf-8") as f:
            data = f.read()
    except (OSError, UnicodeDecodeError) as e:
        die(str(e), 1)
    rs = [r for r in map(parse, data.split("\n")) if r and (not since or r[1] >= since) and (not meth or r[2] == meth)]
    if cmd == "count":
        print(len(rs))
    elif cmd == "bytes":
        print(sum(r[5] for r in rs))
    elif cmd == "ips":
        print(len({r[0] for r in rs}))
    elif cmd == "errors":
        print("client=%d server=%d" % (sum(400 <= r[4] < 500 for r in rs), sum(r[4] >= 500 for r in rs)))
    elif cmd == "status":
        c = {}
        for r in rs:
            c[r[4]] = c.get(r[4], 0) + 1
        for k in sorted(c):
            print("%d\t%d" % (k, c[k]))
    else:
        c = {}
        for r in rs:
            c[r[3]] = c.get(r[3], 0) + 1
        for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[:int(pos[0])]:
            print("%s\t%d" % (k, v))

main(sys.argv[1:])
'''

LOGSTAT_MUTANTS = {
    "ignore_tz": mutant(REF_LOGSTAT, ('t = t - off if m.group(10) == "+" else t + off', "pass")),
    "case_sensitive_method": mutant(REF_LOGSTAT, ('meth = o["--method"].upper()', 'meth = o["--method"]')),
    "status_by_count": mutant(REF_LOGSTAT, ("for k in sorted(c):", "for k in sorted(c, key=lambda k: -c[k]):")),
    "no_calendar_check": mutant(REF_LOGSTAT, ("    except ValueError:\n        return None\n    off", "    except ValueError:\n        t = datetime.datetime(2000, 1, 1)\n    off")),
    "since_exclusive": mutant(REF_LOGSTAT, ("r[1] >= since", "r[1] > since")),
    "keep_query": mutant(REF_LOGSTAT, ('m.group(14).split("?")[0]', "m.group(14)")),
    "exit1": mutant(REF_LOGSTAT, ("def die(msg, code=2):", "def die(msg, code=2):\n    code = 1")),
}


# ----------------------------------------------------------------------------- case 3: solution.py

SOLUTION_PROMPT = """# Task: implement `solution.py`

Write a Python 3 module `solution.py` (standard library only) in the current directory that defines the
functions and the class below. Every requirement is checked by automated tests that import the module
(`from solution import ...`) and compare printed results exactly.

## Requirements

1. **`merge_intervals(intervals)`** — `intervals` is a list of `[start, end]` pairs (lists or tuples of ints,
   `start <= end`, in any order). Return a new list of `[start, end]` **lists**, sorted by start, in which
   overlapping intervals are merged: `merge_intervals([[8,10],[1,3],[2,6],[15,18]])` returns
   `[[1, 6], [8, 10], [15, 18]]`. An empty input returns `[]`.
2. **touching intervals** — intervals that touch (the next start equals the current end) are merged too, but
   intervals separated by a gap are not: `[[1,4],[4,5]]` gives `[[1, 5]]`, while `[[1,2],[3,4]]` stays
   `[[1, 2], [3, 4]]` (integers: 2 and 3 are different points).
3. **containment and signs** — an interval inside another disappears into it (`[[1,10],[2,3],[4,5]]` gives
   `[[1, 10]]`); negative numbers and zero-length intervals (`[5,5]`) work; tuples are accepted as input.
4. **no side effects** — `merge_intervals` never modifies its argument, and the lists it returns are new
   objects (changing a result never changes the input, and vice versa).
5. **`LRUCache(capacity)`** — a least-recently-used cache. `get(key)` returns the stored value, or `-1` if the key
   is absent. `put(key, value)` stores the value. When storing a new key would make the cache hold more than
   `capacity` items, the least recently used item is evicted first. Both `get` (of an existing key) and `put` count
   as a use. LeetCode example: capacity 2; put(1,1); put(2,2); get(1) -> 1; put(3,3) evicts key 2; get(2) -> -1;
   put(4,4) evicts key 1; get(1) -> -1; get(3) -> 3; get(4) -> 4.
6. **updating a key** — `put` of an existing key replaces its value, makes it the most recently used and never
   evicts anything.
7. **`keys()`** — returns a new list of the current keys ordered from least recently used to most recently used.
   A `get` of an absent key does not change the order and does not insert anything.
8. **`len(cache)` and validation** — `len(cache)` is the number of stored items. `LRUCache(capacity)` raises
   `ValueError` if `capacity` is less than 1. Any value (including `0`, `None`, `False`) may be stored and is
   returned as stored; only an absent key returns `-1`.
9. **`roman_to_int(s)`** — converts an uppercase Roman numeral to an int (`'III'` -> 3, `'LVIII'` -> 58,
   `'MCMXCIV'` -> 1994; subtractive pairs such as `IV`, `IX`, `XL`, `XC`, `CD`, `CM` work). It raises `ValueError`
   if `s` is empty or contains any character other than `IVXLCDM` (so lowercase input raises). No further
   validation of the numeral's structure is required.
10. **`longest_common_prefix(strs)`** — the longest common prefix of a list of strings. `['flower','flow','flight']`
    gives `'fl'`; if there is none, or the list is empty, or any string is empty, it returns `''`; a one-element
    list returns that element.
11. **`is_valid_parentheses(s)`** — `True` if the brackets `()`, `[]`, `{}` in `s` are correctly nested and
    closed, else `False`. All other characters are ignored; the empty string is valid. `'([)]'` and `')('` are
    invalid.
12. **`top_k_frequent(nums, k)`** — the `k` most frequent values of the int list `nums`, ordered by frequency
    descending, ties broken by value ascending. If `k` is larger than the number of distinct values, return all
    of them; if `k <= 0` or `nums` is empty, return `[]`.

When you are done, `solution.py` must be in the current directory.
"""

REF_SOLUTION = '''
from collections import OrderedDict, Counter

def merge_intervals(intervals):
    out = []
    for s, e in sorted(([a, b] for a, b in intervals), key=lambda p: p[0]):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out

class LRUCache:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.cap, self.d = capacity, OrderedDict()
    def get(self, key):
        if key not in self.d:
            return -1
        self.d.move_to_end(key)
        return self.d[key]
    def put(self, key, value):
        self.d[key] = value
        self.d.move_to_end(key)
        if len(self.d) > self.cap:
            self.d.popitem(last=False)
    def keys(self):
        return list(self.d)
    def __len__(self):
        return len(self.d)

def roman_to_int(s):
    v = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    if not s or any(c not in v for c in s):
        raise ValueError(s)
    t = 0
    for i, c in enumerate(s):
        t += -v[c] if i + 1 < len(s) and v[c] < v[s[i + 1]] else v[c]
    return t

def longest_common_prefix(strs):
    if not strs:
        return ""
    a, b = min(strs), max(strs)
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    return a[:i]

def is_valid_parentheses(s):
    st, pair = [], {")": "(", "]": "[", "}": "{"}
    for c in s:
        if c in "([{":
            st.append(c)
        elif c in pair:
            if not st or st.pop() != pair[c]:
                return False
    return not st

def top_k_frequent(nums, k):
    if k <= 0:
        return []
    return [n for n, _ in sorted(Counter(nums).items(), key=lambda kv: (-kv[1], kv[0]))[:k]]
'''

SOLUTION_MUTANTS = {
    "touching_not_merged": mutant(REF_SOLUTION, ("s <= out[-1][1]", "s < out[-1][1]")),
    "mutates_input": mutant(REF_SOLUTION, ("sorted(([a, b] for a, b in intervals), key=lambda p: p[0])",
                                           "(intervals.sort(key=lambda p: p[0]) or intervals)")),
    "get_no_refresh": mutant(REF_SOLUTION, ("        self.d.move_to_end(key)\n        return self.d[key]", "        return self.d[key]")),
    "put_existing_no_refresh": mutant(REF_SOLUTION, ("        self.d[key] = value\n        self.d.move_to_end(key)\n",
                                                     "        if key in self.d:\n            self.d[key] = value\n            return\n        self.d[key] = value\n")),
    "no_cap_check": mutant(REF_SOLUTION, ("        if capacity < 1:\n            raise ValueError(\"capacity must be >= 1\")\n", "")),
    "roman_no_error": mutant(REF_SOLUTION, ("if not s or any(c not in v for c in s):\n        raise ValueError(s)", "s = s.upper()")),
    "lcp_first_str": mutant(REF_SOLUTION, ("a, b = min(strs), max(strs)", "a, b = strs[0], strs[-1]")),
    "paren_no_ignore": mutant(REF_SOLUTION, ("        elif c in pair:", "        else:")),
    "topk_tie_desc": mutant(REF_SOLUTION, ("(-kv[1], kv[0])", "(-kv[1], -kv[0])")),
}


def solution_commands():
    C = []

    def add(group, code, out):
        C.append({"group": group, "cmd": "python3 -c '%s'" % code.strip(), "stdout": out, "exit_code": 0})

    imp = "from solution import merge_intervals as m\n"
    add("R1", imp + 'print(m([[8,10],[1,3],[2,6],[15,18]]))\nprint(m([[3,5],[1,2]]))\nprint(m([[1,3],[2,6],[8,10],[7,9]]))', "[[1, 6], [8, 10], [15, 18]]\n[[1, 2], [3, 5]]\n[[1, 6], [7, 10]]\n")
    add("R2", imp + 'print(m([[1,4],[4,5]]))\nprint(m([[1,2],[3,4]]))\nprint(m([[0,0],[0,0],[1,1]]))\nprint(m([[5,6],[1,5],[6,7]]))', "[[1, 5]]\n[[1, 2], [3, 4]]\n[[0, 0], [1, 1]]\n[[1, 7]]\n")
    add("R3", imp + 'print(m([[1,10],[2,3],[4,5]]))\nprint(m([(-3,-1),(-2,2),(5,5)]))\nprint(m([]))\nprint(m([[7,7]]))\nprint(m([[2,9],[1,4],[3,3]]))', "[[1, 10]]\n[[-3, 2], [5, 5]]\n[]\n[[7, 7]]\n[[1, 9]]\n")
    add("R4", imp + 'a=[[3,4],[1,2],[2,3]]\nr=m(a)\nprint(a,r)\nr[0][0]=99\nprint(a)\nb=[[1,2]]\nr=m(b)\nr[0][1]=7\nprint(b, type(r[0]).__name__, r is b, r[0] is b[0])\nt=[(5,6)]\nprint(m(t), t)', "[[3, 4], [1, 2], [2, 3]] [[1, 4]]\n[[3, 4], [1, 2], [2, 3]]\n[[1, 2]] list False False\n[[5, 6]] [(5, 6)]\n")
    lru = "from solution import LRUCache as L\n"
    add("R5", lru + 'c=L(2)\nc.put(1,1)\nc.put(2,2)\nprint(c.get(1))\nc.put(3,3)\nprint(c.get(2))\nc.put(4,4)\nprint(c.get(1))\nprint(c.get(3))\nprint(c.get(4))', "1\n-1\n-1\n3\n4\n")
    add("R5", lru + 'c=L(1)\nc.put("a",1)\nc.put("b",2)\nprint(c.get("a"), c.get("b"))\nd=L(3)\nfor i in range(10): d.put(i,i*i)\nprint([d.get(i) for i in range(10)])', "-1 2\n[-1, -1, -1, -1, -1, -1, -1, 49, 64, 81]\n")
    add("R6", lru + 'c=L(2)\nc.put(1,1)\nc.put(2,2)\nc.put(1,10)\nc.put(3,3)\nprint(c.get(1), c.get(2), c.get(3))\nprint(len(c))', "10 -1 3\n2\n")
    add("R6", lru + 'c=L(2)\nc.put("x",1)\nc.put("y",2)\nc.put("y",3)\nc.put("x",4)\nprint(len(c), c.get("x"), c.get("y"))\nc.put("z",5)\nprint(c.get("y"), c.get("x"), c.get("z"))', "2 4 3\n3 -1 5\n")
    add("R7", lru + 'c=L(3)\nfor k in "abc": c.put(k,1)\nprint(c.keys())\nc.get("a")\nprint(c.keys())\nc.put("b",9)\nprint(c.keys())\nc.get("zzz")\nprint(c.keys(), len(c))\nk=c.keys(); k.append("q")\nprint(c.keys())\nc.put("d",0)\nprint(c.keys())', "['a', 'b', 'c']\n['b', 'c', 'a']\n['c', 'a', 'b']\n['c', 'a', 'b'] 3\n['c', 'a', 'b']\n['a', 'b', 'd']\n")
    add("R8", lru + 'c=L(2)\nprint(len(c))\nc.put(1,None)\nc.put(2,0)\nprint(len(c), c.get(1), c.get(2), c.get(3))\nc.put(3,False)\nprint(len(c), c.get(3), c.get(1))\nfor n in (0,-1):\n    try:\n        L(n)\n        print("no error")\n    except ValueError:\n        print("ValueError")', "0\n2 None 0 -1\n2 False -1\nValueError\nValueError\n")
    rom = "from solution import roman_to_int as r\n"
    add("R9", rom + 'print(r("III"), r("LVIII"), r("MCMXCIV"), r("IV"), r("IX"), r("XL"), r("XC"), r("CD"), r("CM"), r("MMMCMXCIX"), r("I"), r("MDCLXVI"))', "3 58 1994 4 9 40 90 400 900 3999 1 1666\n")
    add("R9", rom + 'for s in ("", "iv", "ABC", "X V", "IV1"):\n    try:\n        print(r(s))\n    except ValueError:\n        print("ValueError")', "ValueError\n" * 5)
    lcp = "from solution import longest_common_prefix as p\n"
    add("R10", lcp + 'print(repr(p(["flower","flow","flight"])), repr(p(["dog","racecar","car"])), repr(p([])), repr(p(["a"])), repr(p(["","abc"])))\nprint(repr(p(["abc","abc","abc"])), repr(p(["interview","internet","interval","intern"])), repr(p(["ab","a"])), repr(p(["xyz"])))', "'fl' '' '' 'a' ''\n'abc' 'inter' 'a' 'xyz'\n")
    par = "from solution import is_valid_parentheses as v\n"
    add("R11", par + 'print([v(s) for s in ["()[]{}", "(]", "([)]", "{[]}", "", "(", ")(", "a(b)c", "((()))", "{[}]", "]", "(()", "[(a+b)*{c}]"]])', "[True, False, False, True, True, False, False, True, True, False, False, False, True]\n")
    top = "from solution import top_k_frequent as t\n"
    add("R12", top + 'print(t([1,1,1,2,2,3],2), t([4,4,3,3,2],2), t([-1,-1],5), t([],3), t([5,3,5,3],0), t([5,3,5,3],-1))\nprint(t([1],1), t([3,1,2],5), t([7,7,7,8,8,9,9,9,9],3), t([2,2,1,1],1))', "[1, 2] [3, 4] [-1] [] [] []\n[1] [1, 2, 3] [9, 7, 8] [1]\n")
    return C


# ----------------------------------------------------------------------------- validation

def run_checks(workdir, checks, env_extra=None):
    """Run every command check in workdir; return {group: [failures]} and the set of groups."""
    fails, groups = {}, set()
    for c in checks:
        groups.add(c["group"])
        try:
            p = subprocess.run(["bash", "-c", c["cmd"]], cwd=workdir, capture_output=True, text=True, timeout=60)
            ok = p.stdout == c["stdout"] and p.returncode == c["exit_code"] and \
                p.stderr.startswith(c.get("stderr_startswith", ""))
            if c.get("stderr_startswith") and not p.stderr.strip().count("\n") == 0:
                ok = False  # exactly one stderr line
        except subprocess.TimeoutExpired:
            ok = False
        if not ok:
            fails.setdefault(c["group"], []).append(c["cmd"].splitlines()[-1][:90])
    return fails, groups


def validate(name, setup, checks, ref_files, mutants, expect_ref_pass=True):
    """Reference must pass every check; each mutant must fail at least one group."""
    base = tempfile.mkdtemp(prefix="gp_")
    try:
        def trial(files):
            wd = tempfile.mkdtemp(dir=base)
            for fn, txt in list(setup.items()) + list(files.items()):
                with open(os.path.join(wd, fn), "w", encoding="utf-8", newline="") as f:
                    f.write(txt)
            return run_checks(wd, checks)
        fails, groups = trial(ref_files)
        assert not fails, "%s reference FAILS: %s" % (name, fails)
        print("  %-22s reference: all %d checks / %d groups pass" % (name, len(checks), len(groups)))
        for mn, files in mutants.items():
            fails, groups = trial(files)
            assert fails, "%s mutant %s was not detected" % (name, mn)
            print("  %-22s %-24s fails %d/%d groups: %s" % (name, mn, len(fails), len(groups), ",".join(sorted(fails, key=lambda g: int(g[1:])))))
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main():
    t_text, st = gen_tally(40000, 20261016)
    ts_text, sts = gen_tally(2000, 7)
    log_text = gen_log(20000, 424242)
    for text, s in ((t_text, st), (ts_text, sts)):
        assert len(TallyModel(text).rows) == s["valid"], (len(TallyModel(text).rows), s)
    print("tally big: %d bytes, %s; small: %d bytes, %s; log: %d bytes" % (len(t_text), st, len(ts_text), sts, len(log_text)))

    tal = [("prog_01_tally", t_text), ("prog_01b_tally_small", ts_text)]
    reqs = {}
    for name, text in tal:
        chk = tally_commands(text, None)
        validate(name, {"transactions.csv": text}, chk, {"tally.py": REF_TALLY},
                 dict({"naive_float_casesens": {"tally.py": NAIVE_TALLY}},
                      **{k: {"tally.py": v} for k, v in TALLY_MUTANTS.items()}))
        write_case(name, TALLY_PROMPT, {"transactions.csv": text}, {"commands": chk})
        reqs[name] = (len(chk), sorted({c["group"] for c in chk}, key=lambda g: int(g[1:])))

    chk = logstat_commands(log_text)
    validate("prog_02_logstat", {"access.log": log_text}, chk, {"logstat.py": REF_LOGSTAT},
             {k: {"logstat.py": v} for k, v in LOGSTAT_MUTANTS.items()})
    write_case("prog_02_logstat", LOGSTAT_PROMPT, {"access.log": log_text}, {"commands": chk})
    reqs["prog_02_logstat"] = (len(chk), sorted({c["group"] for c in chk}, key=lambda g: int(g[1:])))

    chk = solution_commands()
    validate("prog_03_solution", {}, chk, {"solution.py": REF_SOLUTION},
             {k: {"solution.py": v} for k, v in SOLUTION_MUTANTS.items()})
    write_case("prog_03_solution", SOLUTION_PROMPT, {}, {"commands": chk})
    reqs["prog_03_solution"] = (len(chk), sorted({c["group"] for c in chk}, key=lambda g: int(g[1:])))
    for k, v in reqs.items():
        print("%-22s %3d command checks, groups %d" % (k, v[0], len(v[1])))


if __name__ == "__main__":
    main()

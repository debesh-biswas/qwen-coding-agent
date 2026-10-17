# Task: implement `logstat.py`

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

# Task: implement `tally.py`

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

7. **invalid rows** — a data line is *valid* only if all of these hold (checked after the trimming of
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
   unquoting, before validating or comparing), e.g. ` 2023-01-05 ,x, Food ,	12.50 ` is the valid row
   `2023-01-05`, `food`, `12.50`. A category name given with `--category` is not trimmed.

When you are done, `tally.py` must be in the current directory.
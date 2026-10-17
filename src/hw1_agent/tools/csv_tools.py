"""CSV tools (stats, row sums, updates, row removal) and the safe calculator."""
import ast
import csv
import io
import math
import os

from ..util import safe_path


def fmt(x):
    if x != x or x in (math.inf, -math.inf):
        return str(x)
    if x == int(x) and abs(x) < 1e15:
        return str(int(x))
    r = repr(round(x, 10))
    return r.rstrip("0").rstrip(".") if "." in r and "e" not in r else r


def num(s):
    try:
        return float(str(s).replace("$", "").replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def norm(s):
    return str(s).strip().lower()


def read_csv(path):
    with open(safe_path(path), newline="", encoding="utf-8-sig") as fh:
        raw = fh.read()
    return (list(csv.reader(io.StringIO(raw, newline=""))), "\r\n" if "\r\n" in raw else "\n",
            raw.endswith(("\n", "\r")))


def write_csv(path, rows, term, trailing):
    buf = io.StringIO(newline="")
    csv.writer(buf, lineterminator=term).writerows(rows)
    text = buf.getvalue()
    if not trailing and text.endswith(term):
        text = text[:-len(term)]
    full = safe_path(path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def col(header, name):
    for i, h in enumerate(header):
        if norm(h) == norm(name):
            return i
    raise ValueError("no column %r; columns are: %s" % (name, ", ".join(h.strip() for h in header)))


def calc(node):
    if isinstance(node, ast.Expression):
        return calc(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = calc(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        a, b = calc(node.left), calc(node.right)
        return a + b if isinstance(node.op, ast.Add) else a - b if isinstance(node.op, ast.Sub) else a * b if isinstance(node.op, ast.Mult) else a / b
    raise ValueError("only numbers and + - * / ( ) are allowed")


def csv_tool(name, a):
    if name == "csv_stats":
        rows, _, _ = read_csv(a["path"])
        c = col(rows[0], a["column"])
        w = col(rows[0], a["where_column"]) if a.get("where_column") else None
        vals, skipped = [], 0
        for r in rows[1:]:
            if w is not None and (w >= len(r) or norm(r[w]) != norm(a.get("where_value", ""))):
                continue
            v = num(r[c]) if c < len(r) else None
            if v is None:
                skipped += 1
            else:
                vals.append(v)
        if not vals:
            return "column=%s rows_matched=%d (no numeric values)" % (a["column"], skipped)
        return "column=%s rows_matched=%d rows_used=%d skipped=%d sum=%s average=%s min=%s max=%s" % (
            a["column"], len(vals) + skipped, len(vals), skipped, fmt(math.fsum(vals)), fmt(math.fsum(vals) / len(vals)),
            fmt(min(vals)), fmt(max(vals)))
    if name == "csv_row_sum":
        rows, _, _ = read_csv(a["path"])
        i = col(rows[0], a["id_column"])
        cols = [col(rows[0], x) for x in a["columns"]]
        out, total = [], []
        for r in rows[1:]:
            if i < len(r) and norm(r[i]) == norm(a["id_value"]):
                vs = [num(r[c]) if c < len(r) else None for c in cols]
                if None in vs:
                    raise ValueError("non-numeric value in row %s=%s" % (a["id_column"], a["id_value"]))
                out.append("row %s=%s: %s = %s" % (a["id_column"], a["id_value"], " + ".join(
                    "%s=%s" % (x, fmt(v)) for x, v in zip(a["columns"], vs)), fmt(math.fsum(vs))))
                total.extend(vs)
        if not out:
            raise ValueError("no row with %s=%s" % (a["id_column"], a["id_value"]))
        if len(out) > 1:
            out.append("TOTAL over all %d matching rows = %s" % (len(out), fmt(math.fsum(total))))
        return "; ".join(out)
    rows, term, trailing = read_csv(a["path"])
    if name == "csv_update":
        i, c = col(rows[0], a["id_column"]), col(rows[0], a["column"])
        n = 0
        for r in rows[1:]:
            if i < len(r) and norm(r[i]) == norm(a["id_value"]):
                if c >= len(r):
                    r.extend([""] * (c + 1 - len(r)))
                r[c] = str(a["new_value"])
                n += 1
        if not n:
            return "ERROR: no row with %s=%s; nothing written" % (a["id_column"], a["id_value"])
        write_csv(a["output"], rows, term, trailing)
        return "Updated %d row%s; wrote %s (%d data rows)" % (n, "" if n == 1 else "s", a["output"], len(rows) - 1)
    c, op, v = col(rows[0], a["column"]), a.get("op"), str(a["value"])
    if op not in ("equals", "not_equals", "contains", "greater_than", "less_than", "at_least", "at_most"):
        raise ValueError("unknown op %r" % op)

    def match(r):
        cell = r[c] if c < len(r) else ""
        if op == "equals":
            return norm(cell) == norm(v)
        if op == "not_equals":
            return norm(cell) != norm(v)
        if op == "contains":
            return norm(v) in norm(cell)
        x, y = num(cell), num(v)
        if x is None or y is None:
            return False
        return {"greater_than": x > y, "less_than": x < y, "at_least": x >= y, "at_most": x <= y}[op]
    kept = [r for r in rows[1:] if not match(r)]
    write_csv(a["output"], rows[:1] + kept, term, trailing)
    return "Removed %d rows, kept %d; wrote %s" % (len(rows) - 1 - len(kept), len(kept), a["output"])

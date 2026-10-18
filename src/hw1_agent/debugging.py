"""Debugging tasks: several independent investigations, per-key majority vote."""
import copy
import json
import os
import time

from . import state as S
from .guards import bad_shape, key_specs
from .loop import loop
from .util import Finish, log, safe_path


def vote_runs(base, sysmsg, path):
    """Debugging tasks: investigate up to 3 times from scratch and take the per-key majority (single runs of a small
    model vary a lot: one wrong hop early on spoils every later key). Ends by raising Finish."""
    final, reports, last = S.DEADLINE, [], None
    for k in range(3):
        if k == 2 and len(reports) >= 2 and reports[0] == reports[-1]:
            break  # two independent investigations agree
        if k and (final - time.monotonic() < 0.3 * (final - S.START) or S.CALLS > 70):
            break
        S.NOTES[:] = []
        S.BOUNCES.clear()
        if k and os.path.isfile(safe_path(path)):
            os.remove(safe_path(path))
        S.DEADLINE = time.monotonic() + (final - time.monotonic()) * (0.55 if k == 0 else 0.6 if k == 1 else 1)
        try:
            loop(copy.deepcopy(base), sysmsg, max(5, min(S.MAX_TURNS, 110 - S.CALLS)), 0 if k == 0 else 0.6)
        except Finish as f:
            last = f
        finally:
            S.DEADLINE = final
        try:
            with open(safe_path(path), encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict) and d:
                reports.append(d)
                log("ATTEMPT %d REPORT: %s" % (k + 1, json.dumps(d)[:600]))
        except (OSError, ValueError):
            log("ATTEMPT %d: no usable %s" % (k + 1, path))
    if not reports:
        raise last or Finish("error", "No %s was produced." % path)
    voted, specs = {}, key_specs()
    for key in [k for r in reports for k in r]:
        if key not in voted:
            vals = [json.dumps(r[key], sort_keys=True) for r in reports if key in r]
            vals = [v for v in vals if not bad_shape(specs.get(key, ""), json.loads(v))] or vals  # drop misfits
            voted[key] = json.loads(max(vals, key=lambda v: (vals.count(v), -vals.index(v))))  # ties: earliest run
    with open(safe_path(path), "w", encoding="utf-8") as fh:
        json.dump(voted, fh, indent=2, ensure_ascii=False)
    raise Finish("success", "Wrote %s (per-key majority of %d independent investigations): %s" % (
        path, len(reports), json.dumps(voted, ensure_ascii=False)))

"""Verifier for fix-median. Runs INSIDE the sandbox after the agent finishes.
Exit 0 = pass. Stdlib only; imports the agent-modified stats.py from cwd."""

import subprocess
import sys

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} {detail}")
        failures.append(name)


# 1. Static behavior checks: import the module from the working directory.
sys.path.insert(0, "")
import stats  # noqa: E402

try:
    check("median even -> mean of middles", stats.median([1, 2, 3, 4]) == 2.5)
except Exception as err:
    check("median even -> mean of middles", False, f"({err})")

try:
    check("median odd -> middle element", stats.median([3, 1, 2]) == 2.0)
except Exception as err:
    check("median odd -> middle element", False, f"({err})")

try:
    stats.median([])
    check("median empty raises ValueError", False, "(no exception)")
except ValueError:
    check("median empty raises ValueError", True)
except Exception as err:
    check("median empty raises ValueError", False, f"({type(err).__name__}: {err})")

try:
    check("median returns float", isinstance(stats.median([1, 3]), float))
except Exception as err:
    check("median returns float", False, f"({err})")

try:
    check("mean untouched", stats.mean([2, 4]) == 3.0)
except Exception as err:
    check("mean untouched", False, f"({err})")

# 2. CLI check: exact documented output for `python3 stats.py 3 1 2`.
proc = subprocess.run(
    [sys.executable, "stats.py", "3", "1", "2"], capture_output=True, text=True
)
expected = "mean: 2.0\nmedian: 2.0\n"
check(
    "cli output exact",
    proc.returncode == 0 and proc.stdout == expected,
    f"(exit={proc.returncode}, stdout={proc.stdout!r})",
)

print()
if failures:
    print(f"{len(failures)} check(s) failed")
    sys.exit(1)
print("all checks passed")

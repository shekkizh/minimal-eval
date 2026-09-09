"""Verifier for json2csv. Runs INSIDE the sandbox after the agent finishes."""

import json
import subprocess
import sys
import tempfile
import os

failures = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name} {detail}")
        failures.append(name)


def run_case(name, records, expected_stdout):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(records, f)
        path = f.name
    try:
        proc = subprocess.run(
            [sys.executable, "json2csv.py", path], capture_output=True, text=True
        )
        check(
            name,
            proc.returncode == 0 and proc.stdout == expected_stdout,
            f"(exit={proc.returncode}, stdout={proc.stdout!r}, stderr={proc.stderr[:200]!r})",
        )
    finally:
        os.unlink(path)


run_case(
    "example from spec",
    [{"a": 1, "b": None}, {"c": True, "a": 2}],
    "a,b,c\n1,,\n2,,true\n",
)

run_case(
    "empty array -> no output",
    [],
    "",
)

run_case(
    "single row, unsorted keys",
    [{"z": 26, "a": 1}],
    "a,z\n1,26\n",
)

run_case(
    "string values with comma quoting",
    [{"name": "Doe, Jane", "age": 30}],
    'age,name\n30,"Doe, Jane"\n',
)

run_case(
    "numbers keep numeric formatting",
    [{"x": 1.5, "y": -2}],
    "x,y\n1.5,-2\n",
)

print()
if failures:
    print(f"{len(failures)} check(s) failed")
    sys.exit(1)
print("all checks passed")

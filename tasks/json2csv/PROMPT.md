# Write `json2csv.py`

Create a script `json2csv.py` in the working directory that converts a JSON
array of flat objects to CSV.

Behavior:

- `python3 json2csv.py input.json` reads the file and writes CSV to stdout.
- The header row is the union of all keys, **sorted alphabetically**.
- An empty input array produces no output.
- Rows follow the input order; a missing key becomes an empty cell.
- Values `None` become empty cells; booleans become `true`/`false` (lowercase).

Example — `input.json`:

```json
[{"a": 1, "b": null}, {"c": true, "a": 2}]
```

Running `python3 json2csv.py input.json` prints exactly:

```
a,b,c
1,,
2,,true
```

Only the Python standard library may be used. Test your script before
finishing — you can create your own test input files.

# Fix `stats.py`

`stats.py` in the working directory computes statistics for a list of numbers,
but it currently violates its specification and crashes in some cases.

The documented behavior is:

- `median(nums)` — middle value for odd-length input; **the mean of the two
  middle values** for even-length input (e.g. `median([1, 2, 3, 4]) == 2.5`).
  It must return a `float` and raise `ValueError` on an empty list.
- `mean(nums)` — arithmetic mean; raise `ValueError` on an empty list.
- Running `python3 stats.py 3 1 2` must print exactly:

  ```
  mean: 2.0
  median: 2.0
  ```

Fix `stats.py` so that all of the above holds. Do not change the function
names or signatures. Verify your fix by running the script yourself.

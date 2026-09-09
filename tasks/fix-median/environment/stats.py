#!/usr/bin/env python3
"""Buggy statistics script — the median of an even-length list is wrong
(it picks an element instead of averaging the two middle values)."""


def mean(nums):
    if not nums:
        raise ValueError("empty list")
    return sum(nums) / len(nums)


def median(nums):
    if not nums:
        raise ValueError("empty list")
    s = sorted(nums)
    return float(s[len(s) // 2])


if __name__ == "__main__":
    import sys

    nums = [float(x) for x in sys.argv[1:]]
    print(f"mean: {mean(nums)}")
    print(f"median: {median(nums)}")

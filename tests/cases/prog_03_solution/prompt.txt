# Task: implement `solution.py`

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

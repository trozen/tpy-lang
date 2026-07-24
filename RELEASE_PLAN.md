# Release plan

The milestone slice of `BUGS.md` / `TODO.md`. Short bullets only -- each
item's full entry lives in the file it points to (search the quoted phrase
there). When a release ships, delete its section and promote the next one.

## 0.6.0 (queued features; details in TODO.md / `_work.md` where tracked)

- `@classmethod` / `cls` -- alternate constructors
- `collections.defaultdict` + `deque` (+ `OrderedDict`)
- Nested / multi-`for` comprehensions (list/dict/set + genexprs)
- `input(prompt)` -- fix the opaque "No matching overload" diagnostic
- Extend the stale-capture warning to in-place mutation of captured
  containers (the fixable half of the closure-snapshot divergence)

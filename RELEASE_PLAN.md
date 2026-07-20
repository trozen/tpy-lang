# Release plan

The milestone slice of `BUGS.md` / `TODO.md`. Short bullets only -- each
item's full entry lives in the file it points to (search the quoted phrase
there). When a release ships, delete its section and promote the next one.

## 0.5.0 (target: days away; bug fixes only, no new features)

### Bugs to fix (the `[HIGH]` entries in BUGS.md)

- "renders pointer truthiness instead of dispatching to the narrowed
  record's `__bool__`" -- narrowed Optional record skips the dunder
- "compiles to an empty loop" -- key-iteration over a narrowed Optional dict
- "dict subscript narrows the key to int32" -- valid BigInt key panics where
  CPython works
- "hashes inputs AFTER the link" -- build cache can persistently serve a
  stale binary after a mid-build source edit

### Ships as known limitation (document in release notes, don't fix)

- The borrow/view-lifetime UAF cluster (BUGS.md Safety section, the
  `deferred: MIR` entries + related MED items) -- retired wholesale by the
  planned MIR ownership checker, not patched piecemeal
- Escaping-closure capture-by-value snapshot semantics -- inherent to the
  zero-cost closure model; the warning extension is queued for 0.6.0

## 0.6.0 (queued features; details in TODO.md / `_work.md` where tracked)

- `@classmethod` / `cls` -- alternate constructors
- `collections.defaultdict` + `deque` (+ `OrderedDict`)
- Nested / multi-`for` comprehensions (list/dict/set + genexprs)
- `input(prompt)` -- fix the opaque "No matching overload" diagnostic
- Toolchain preflight: reject clang < 19 with libstdc++; add the Python
  3.12+ floor guard
- Extend the stale-capture warning to in-place mutation of captured
  containers (the fixable half of the closure-snapshot divergence)

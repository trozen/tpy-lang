# CPython interop tests

Runtime verification for CPython extension authoring
(`docs/CPYTHON_INTEROP.md`). The comp-phase rejection cases live under
`tests/cases/interop/`; this directory holds the **ext-exec** cases, driven by
`tests/test_interop_exec.py` as part of the normal pytest suite.

## Running

```
uv run pytest tests/test_interop_exec.py            # ext-exec + facade self-check
uv run pytest tests/test_interop_exec.py --no-exec  # codegen/snapshot only
uv run python tests/update_snapshots.py -k funcs    # regenerate expected/
```

For each case directory (any `tests/interop/<case>/` with a `driver.py`) the
harness mirrors the main snapshot harness:

- **comp/snapshot** (always): `tpyc` emits the module `.hpp`/`.cpp` and the
  CPython glue `<mod>_ext.cpp`; all three are snapshotted into `expected/`.
  This alone catches any glue/marshaller codegen drift in CI.
- **ext-exec** (cached, like the exec phase; **Linux-only**): `tpyc`'s
  first-class `.so` build mode (`-b` on an `# tpy: ext_module` -> `-fPIC
  -shared`, no `main`, facade only -- never links libpython) produces an
  importable `<mod>.so`; CPython imports it and runs `driver.py`. Gated by a
  content-addressed marker (the same shared `exec-results/` cache the exec
  phase uses) so it re-verifies once after any relevant change, then skips.
  The `-shared` link recipe is Linux-only for now (macOS needs `-bundle
  -undefined dynamic_lookup`), so this phase skips on other platforms; the
  snapshot, cpy-parity, and facade self-check still run everywhere.
- **cpy-parity** (always, cheap): the *same* `driver.py` over the TPy source
  (via the `lib/cpy` stubs) must reproduce the ext-exec output snapshot.
- **ext_checks.py** (ext-only, if present): runs against the built `.so`.

`test_facade_selfcheck` compiles the hand-mirrored facade
(`runtime/cpp/tests/interop/cpython_facade_selfcheck.cpp`) against the real
Python ABI once, turning a mirroring slip into a compile error. It is
**skipped** (not failed) when Python dev headers (`Python.h`) are unavailable;
the ext-exec build itself needs no headers (facade only), and the import runs
under the host interpreter.

`driver.py` is byte-identical across the ext-exec and cpy-parity runs; only
what `import <mod>` resolves to changes (compiled `.so` vs interpreted source).
Adding a case is just a new directory with a `driver.py` -- the harness
discovers it. Generated C++ and the built `.so` land in the gitignored
`tests/interop/<case>/__tpyc__/` for inspection.

Requires a C++ toolchain and CPython >= 3.12 (the abi3 floor); the facade
self-check additionally requires `Python.h`.

## Cases

- **`funcs/`** (`funcs.py`) -- the free-function surface: `answer()` (no-arg
  `Int64`, `METH_NOARGS`), `add(a: Int64, b: Int64)` (rung 1, argument
  marshalling, `METH_VARARGS`), and `big_square`/`negate` over `int` (rung 2,
  BigInt, including values beyond int64 that cross via the hex round-trip).
  `ext_checks.py` covers the marshalling-error cases (`OverflowError` on an
  out-of-`Int64` int, `TypeError` on a non-integer) -- which deliberately
  diverge from the source, where `Int64` is just an annotation and `int` is
  unbounded, so they are checked only against the `.so`.

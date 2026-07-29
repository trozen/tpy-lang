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

For each case directory (any `tests/interop/<case>/` whose `src/` holds a
`driver.py`) the harness mirrors the main snapshot harness. Inputs -- the
`# tpy: ext_module` module, `driver.py`, optional `ext_checks.py` -- live in
`src/`, mirroring the `tests/cases` input/output split; `expected/` and the
gitignored `__tpyc__/` build output stay at the case root:

- **comp/snapshot** (always): `tpyc` emits the module `.hpp`/`.cpp` and the
  CPython glue `<mod>_ext.cpp`; all three are snapshotted into `expected/`,
  and the front-end diagnostics into `expected/diag.txt` (source paths
  normalized to basenames, as in the main harness). `# tpyc:` inline
  annotations on `src/*.py` are validated against the diagnostics. This
  alone catches any glue/marshaller codegen or diagnostics drift in CI.
- **ext-exec** (cached, like the exec phase; **Linux-only**): `tpyc`'s
  first-class `.so` build mode (`-b` on an `# tpy: ext_module` -> `-fPIC
  -shared`, no `main`, facade only -- never links libpython) produces an
  importable `<mod>.so`; CPython imports it and runs `driver.py`. Gated by a
  content-addressed marker (the same shared `exec-results/` cache the exec
  phase uses) so it re-verifies once after any relevant change, then skips.
  The `-shared` link recipe is Linux-only for now (macOS needs `-bundle
  -undefined dynamic_lookup`), so this phase skips on other platforms; the
  snapshot, cpy-parity, and facade self-check still run everywhere.
- **THIR overlay** (always, when THIR is on): the case's module is re-emitted
  through THIR and byte-diffed against the AST oracle, and an unmarked case
  must route every user body (the ratchet) -- the same contract the main
  harness enforces, with `no_thir.txt` at the case root exempting a case from
  the ratchet only, never from the diff. `--thir-classify` /
  `--thir-check-flip` maintain these markers here too. Interop cases are
  tallied on their own `tpy| interop thir:` line and deliberately kept out of
  the migration dial, which is keyed to `tests/cases`.

  The overlay emits *both* sides itself instead of diffing THIR against
  `expected/`: the snapshot above comes from the real `tpyc` CLI at the
  default `emit_source_comments=False`, so a THIR-vs-snapshot diff cannot see
  a source-comment divergence at all. Emitting both sides with comments on
  restores that sensitivity. The glue `_ext.cpp` is diffed too -- it has no
  THIR path today, so the diff pins that it stays THIR-insensitive.
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
Adding a case is just a new directory with a `src/driver.py` -- the harness
discovers it. Generated C++ and the built `.so` land in the gitignored
`tests/interop/<case>/__tpyc__/` for inspection.

Requires a C++ toolchain and CPython >= 3.12 (the abi3 floor); the facade
self-check additionally requires `Python.h`.

## Cases

- **`funcs/`** (`src/funcs.py`) -- the free-function surface: `answer()` (no-arg
  `Int64`, `METH_NOARGS`), `add(a: Int64, b: Int64)` (rung 1, argument
  marshalling, `METH_VARARGS`), and `big_square`/`negate` over `int` (rung 2,
  BigInt, including values beyond int64 that cross via the hex round-trip).
  `ext_checks.py` covers the marshalling-error cases (`OverflowError` on an
  out-of-`Int64` int, `TypeError` on a non-integer) -- which deliberately
  diverge from the source, where `Int64` is just an annotation and `int` is
  unbounded, so they are checked only against the `.so`.
- **`floats/`** (`floats.py`) -- the `float` boundary (rung 3): `scale(x: float)`
  and `addf(a, b: float)` marshal as C++ `double` (`int`/`bool` args coerce via
  `__float__`). Its boundary errors (non-number, int-too-large-for-double) match
  CPython for the values driven, so they are parity-checked in `driver.py`
  rather than an ext-only file.
- **`int_widths/`** (`int_widths.py`) -- every fixed-width int boundary
  (`Int8`..`Int64` / `UInt8`..`UInt64`): each `iN`/`uN` round-trips its width
  through the matching C++ type with a per-width range check. `driver.py`
  exercises the min/max of each width (in-range, so source-parity holds);
  `ext_checks.py` covers the ext-only divergences -- out-of-range and
  negative-into-unsigned raise `OverflowError`, a non-integer `TypeError`,
  while the unbounded source accepts them.
- **`bools/`** (`bools.py`) -- the `bool` boundary plus a void-return `@export`:
  `flip`/`both`/`identity` marshal as C++ `bool` (any arg coerces by truthiness,
  via `PyObject_IsTrue` / `PyBool_FromLong`), and `tally(b) -> None` returns
  `Py_None` (its effect read back through an `Int64` getter, since the
  extension must not print). `ext_checks.py` covers the truthiness coercions
  observable only through `identity` (which the source returns unchanged) and a
  `__bool__`-raising argument -- both ext-only divergences from the source.
- **`strings/`** (`strings.py`) -- the `str` boundary (copy-in): `echo`,
  `shout` (`.upper()`), and `greet` (concat) marshal PyUnicode <-> `std::string`
  (the wrapper marshals the owned form, the function takes the `std::string_view`
  borrow by implicit conversion). `driver.py` round-trips empty / multibyte
  UTF-8 / transformed values (all source-parity); `ext_checks.py` covers the
  ext-only divergences -- a non-`str` arg raises `TypeError`, a lone-surrogate
  `str` raises `UnicodeEncodeError`, while the unbounded source accepts them.
- **`bytes_vals/`** (`bytes_vals.py`) -- the `bytes` boundary (copy-in): `echo`,
  `cat` (concat), `shout` (`.upper()`) marshal PyBytes <-> `std::vector<uint8_t>`
  (owned form in the wrapper, `std::span<const uint8_t>` borrow in the function).
  `driver.py` round-trips empty / raw-with-NUL-and-high-byte / concatenated
  values; `ext_checks.py` covers the ext-only `TypeError`s -- a `str`, a
  `bytearray` (mutable buffer, rejected by value), and a non-bytes arg.
- **`kwargs/`** (`kwargs.py`) -- keyword-argument marshalling
  (`PyArg_ParseTupleAndKeywords`): free functions and a class `__init__` +
  methods accept their params positionally, all-keyword, mixed, and
  reordered-by-keyword (`Vec(x=1, y=2)`, `v.move(dx=10, dy=20)`). A keyword
  call mutates through and is observed on the same object -- the reference-
  semantics proof, here driven by keyword args. All forms are source-parity, so
  the whole driver is checked against both the `.so` and the Python source.
- **`enums/`** (`enums.py`) -- `@export` enums recreated as real CPython enum
  types at `PyInit_` (the `enum` functional API via `enum_bridge.hpp`): an
  `IntEnum` (`Color`) and a plain `Enum` (`Direction`) covering members,
  `.name`/`.value`, member identity, iteration, value/name lookup,
  `__name__`/`__qualname__`/`__module__`, and the IntEnum-vs-Enum `== int` /
  `isinstance(_, int)` distinction. All source-parity (a class-statement enum is
  observably identical), so the whole driver runs against both the `.so` and the
  source -- no `ext_checks.py` needed.
- **`constants/`** (`constants.py`) -- module-level `Final` constants exposed as
  init-time module-attribute snapshots: `int` (incl. a value beyond int64 via
  the BigInt hex round-trip), `float`, `bool`, `str`. `ext_checks.py` asserts a
  `Final[Char]` (a non-boundary type) is *not* exposed -- it exists on the
  source but not the `.so`, so that check is ext-only.

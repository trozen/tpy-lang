# CPython interop tests

Verification for CPython extension authoring (`docs/CPYTHON_INTEROP.md`).

## Running

```
bash tests/interop/verify.sh
```

It compiles the facade layout self-check against the real Python ABI once,
then for each case directory (any `tests/interop/<case>/` with a `driver.py`):
runs `tpyc` to emit the module `.cpp` + extension glue TU, builds the `.so`
from the generated C++ (facade only -- no `Python.h`, no libpython), imports
it under CPython and runs `driver.py` (**ext-exec**), runs the *same*
`driver.py` over the TPy source via the `lib/cpy` stubs (**cpy-parity**) and
asserts they match, and runs `ext_checks.py` if present.

`driver.py` is byte-identical across the ext-exec and cpy-parity runs; only
what the imported module resolves to changes (compiled `.so` vs interpreted
source). That is the TPy-vs-CPython parity shape the future **ext-exec
snapshot-harness variant** will adopt -- this script is the interim until
that lands. Adding a case is just a new directory; the script discovers it.

The generated C++ and the built `.so` are left in (gitignored)
`tests/interop/build/<case>/` so they can be inspected after a run:
`out/src/<mod>.cpp` (module codegen), `out/src/<mod>_ext.cpp` (the CPython
glue TU -- `PyInit_`, `PyMethodDef`, wrappers), and `built/<mod>.so`.

Requires a C++ toolchain, Python dev headers (`Python.h`), and CPython
>= 3.12 (the abi3 floor).

## Cases

- **`funcs/`** (`funcs.py`) -- the free-function surface: `answer()` (no-arg
  `Int64`, `METH_NOARGS`), `add(a: Int64, b: Int64)` (rung 1, argument
  marshalling, `METH_VARARGS`), and `big_square`/`negate` over `int` (rung 2,
  BigInt, including values beyond int64 that cross via the hex round-trip).
  `ext_checks.py` covers the marshalling-error cases (`OverflowError` on an
  out-of-`Int64` int, `TypeError` on a non-integer) -- which deliberately
  diverge from the source, where `Int64` is just an annotation and `int` is
  unbounded, so they are checked only against the `.so`.

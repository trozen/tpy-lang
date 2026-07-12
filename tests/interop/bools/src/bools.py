# tpy: ext_module
# CPython extension over the bool boundary plus a void-return @export:
#  - bool params/returns marshal via PyObject_IsTrue / PyBool_FromLong, so any
#    argument coerces by truthiness exactly like CPython's bool();
#  - `tally(b) -> None` returns Py_None. Its effect is observed through a
#    marshalled Int64 getter rather than print: the extension's stdout buffers
#    separately from the driver's, so a void function must not print or the
#    ext-exec and cpy-parity runs would interleave differently.
from tpy import Int64
from tpy.extern import export


_true_count: Int64 = 0


@export
def flip(b: bool) -> bool:
    return not b


@export
def both(a: bool, b: bool) -> bool:
    return a and b


@export
def identity(b: bool) -> bool:
    return b


@export
def tally(b: bool) -> None:
    global _true_count
    if b:
        _true_count += 1


@export
def true_count() -> Int64:
    return _true_count

# tpy: ext_module
# CPython extension exercising the exception bridge: a TPy exception raised in
# an @export body crosses to the caller as the matching CPython exception type
# with its message preserved, instead of collapsing to RuntimeError. Covers a
# direct Exception subclass (ValueError), a deeper LookupError leaf (KeyError),
# an OSError-tree leaf (FileNotFoundError), a runtime-raised ZeroDivisionError,
# and a bare Exception.
from tpy import int64
from tpy.extern import export


@export
def check_positive(n: int64) -> int64:
    if n < 0:
        raise ValueError("must be non-negative")
    return n


@export
def lookup(key: int64) -> int64:
    if key != 1:
        raise KeyError("no such key")
    return 100


@export
def open_missing() -> int64:
    raise FileNotFoundError("missing file")


@export
def divide(a: int64, b: int64) -> int64:
    return a // b  # b == 0 raises ZeroDivisionError from the runtime


@export
def fail_generic() -> int64:
    raise Exception("generic failure")

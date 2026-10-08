# tpy: ext_module
# CPython extension exercising the exception bridge: a TPy exception raised in
# an @export body crosses to the caller as the matching CPython exception type
# with its message preserved, instead of collapsing to RuntimeError. Covers a
# direct Exception subclass (ValueError), a deeper LookupError leaf (KeyError),
# an OSError-tree leaf (FileNotFoundError), a runtime-raised ZeroDivisionError,
# a bare Exception, and SystemExit, which crosses as its `code` (int / str /
# None) rather than its message, also from user subclasses with and without
# data fields.
from tpy import int32, int64
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


class Leave(SystemExit):
    pass


class LeaveWith(SystemExit):
    reason: str

    def __init__(self, code: int32, reason: str) -> None:
        super().__init__(code)
        self.reason = reason


# SystemExit's code crosses as the int, str or None the body raised.
@export
def exit_int() -> int64:
    raise SystemExit(3)


@export
def exit_str() -> int64:
    raise SystemExit("m")


@export
def exit_bare() -> int64:
    raise SystemExit()


@export
def exit_none() -> int64:
    raise SystemExit(None)


@export
def exit_sub() -> int64:
    raise Leave(9)


@export
def exit_sub_data() -> int64:
    raise LeaveWith(4, "why")

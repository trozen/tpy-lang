# A nested def reading an enclosing local an `except ... as e` handler that
# falls through deletes: after the handler the closure's read is CPython's
# UnboundLocalError on that path.
from tpy import int32


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


def f(flag: bool) -> None:
    e = Box(1)
    try:
        if flag:
            raise ValueError("x")
    except ValueError as e:
        print("h")

    def inner() -> int32:
        # The capture of `e`, unbound after the handler.
        return e.v  # tpyc: error(/variable 'e' may not be assigned at this point/)
    print(inner())


f(False)

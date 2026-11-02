# A nested def reading an enclosing local bound on only one `if` arm: on the
# other path the closure's read is CPython's UnboundLocalError, so it is
# refused at the read (an `except ... as` that falls through and a `del`
# leave the name unassigned the same way).
from tpy import int32


def f(flag: bool) -> None:
    if flag:
        x = 1

    def inner() -> int32:
        # The capture of `x`, unassigned when `flag` is false.
        return x  # tpyc: error(/variable 'x' may not be assigned at this point/)
    print(inner())


f(True)

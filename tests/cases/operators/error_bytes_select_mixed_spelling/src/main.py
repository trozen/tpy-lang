# A `bytes` select whose operands have DIFFERENT runtime spellings keeps
# rejecting: a param (a span) against an owned return has no common ternary
# type and neither conversion is safe.
# See BUGS.md#bytes-select-mixed-runtime-spelling.


def f() -> bytes:
    return b"xy"


def g(a: bytes) -> None:
    # `a` spells the span, `f()` the owned vector -- a span over the temporary
    # would dangle, and owning `a` would copy where the row never copies.
    v = a or f()  # tpyc: error(/binop\.shape\.\|\|/)
    print(len(v))


def main() -> None:
    g(b"ab")


main()

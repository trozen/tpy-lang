# A UNION-returning call at a native protocol argument slot: only the open-T
# result is admitted there, and the union keeps its own lift rung, so
# `repr(pick(f))` is rejected.
from tpy import Int32


def pick(f: bool) -> Int32 | str:
    if f:
        return 1
    return "a"


def show(f: bool) -> None:
    print(repr(pick(f)))  # tpyc: error(/call.native_arg.union/)


def main() -> None:
    show(True)


main()

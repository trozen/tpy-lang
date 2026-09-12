# Target shapes for the global-walrus write beyond the plain int global: a
# method receiver, float/bool/str globals, and the `if (g := f()) is not None:`
# idiom, which must narrow the binding for the branch body while the write
# still lands on the module variable.

from tpy import int32

ratio = 0.0
flag = False
title = "start"
slot: int32 | None = None


def lookup(n: int32) -> int32 | None:
    return n if n > 0 else None


def narrow(n: int32) -> int32:
    global slot
    if (slot := lookup(n)) is not None:  # tpyc: ok
        return slot
    return -1


class Widget:
    def retitle(self, t: str) -> int:
        # A method writes the global through the same route a free function does.
        global title
        return len(title := t)


def other_types() -> None:
    global ratio, flag
    r = (ratio := 1.5)
    f = (flag := True)
    print("bound:", r, f)


def main() -> None:
    print("narrow:", narrow(4), narrow(-1), slot)
    print("method:", Widget().retitle("renamed"), title)
    other_types()
    print("globals:", ratio, flag)


main()

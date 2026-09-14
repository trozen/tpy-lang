# The one alias clobber the slot model cannot avoid: a loan taken from a
# rebind site's OWN storage that is still live when that site runs again on
# the next iteration -- one slot per site cannot hold two iterations' objects.
# The rebind warns; `copy(p)` or a fresh name for the new value is the fix.
# The printed values are the WRONG ones the warning announces (CPython keeps
# the object the loan was given) -- hence no_cpython.txt.
from tpy import int32, take_ptr


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


# `q` is re-taken of `p` AFTER the in-loop rebind and read before that on the
# next iteration, so the rebind overwrites what `q` points at.
def loop_carried_section() -> None:
    other = Point(21)
    p = Point(22)
    q = take_ptr(other)
    i = 0
    while i < 2:
        p = Point(i)  # tpyc: warning(/will not keep the object it was given/)
        print("loop_carried_iter:", q.x)
        q = take_ptr(p)
        i += 1
    print("loop_carried:", q.x, p.x)


# the `for` spelling
def for_carried_section() -> None:
    p = Point(30)
    q = take_ptr(p)
    for i in range(2):
        p = Point(i)  # tpyc: warning(/will not keep the object it was given/)
        print("for_carried_iter:", q.x)
        q = take_ptr(p)
    print("for_carried:", q.x, p.x)


def main() -> None:
    loop_carried_section()
    for_carried_section()


main()

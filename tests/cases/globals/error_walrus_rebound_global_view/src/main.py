# A `global` name rebound by a WALRUS is a rebind like any other, so a view
# returned off it is rejected. The rebind fact pairs each body's `global`
# declarations with the names that body binds, and a walrus binds inside an
# expression where a statement-target walk cannot see it -- the plain `=`
# spelling is pinned by `error_view_returned_off_rebound_global`, and the
# `while` head is the same enumerator.
from tpy import StrView

S = "hello world, long enough that no small-string buffer hides the reuse"


def view() -> StrView:
    return S  # tpyc: error(/Cannot return a borrow of module variable 'S'.*'reset' rebinds/)


def reset() -> None:
    global S
    if (S := "x"):
        pass


def main() -> None:
    v = view()
    reset()
    print(v)


main()

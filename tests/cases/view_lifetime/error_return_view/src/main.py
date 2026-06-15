# Returning a borrowing view of a local is rejected: a view-returning method
# (str.strip) borrows its receiver, so `return a.strip()` returns a view of the
# local `a` that dies at return. (f-string returns: error_return_fstring.)
from tpy import StrView


def make() -> str:
    return "this is long enough to dodge the small-string buffer"


def ret_strip() -> StrView:
    a = make()
    return a.strip()  # tpyc: error(/StrView referencing a local or temporary/)


def main() -> None:
    print(ret_strip())


main()

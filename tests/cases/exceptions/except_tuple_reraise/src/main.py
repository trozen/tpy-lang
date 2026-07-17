# A bare `raise` inside an `except (A, B):` body. Each expanded arm re-raises the
# exception it actually caught, so the original dynamic type reaches the outer
# handler -- the arm duplication must not collapse both types into one.
class AErr(Exception):
    pass


class BErr(Exception):
    pass


def inner(which: int) -> None:
    try:
        if which == 0:
            raise AErr()
        raise BErr()
    except (AErr, BErr):  # tpyc: ok
        print("inner saw it, re-raising")
        raise


def main() -> None:
    for i in range(2):
        try:
            inner(i)
        except AErr:
            print("outer: AErr")
        except BErr:
            print("outer: BErr")


main()

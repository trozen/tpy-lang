# Error: the `except (A, B):` tuple form is rejected on a return-tier
# (ReturnException) try/except, which carries a single error type in its
# std::optional error slot. The diagnostic names the tuple form rather than the
# expanded handler count, so it matches what the user actually wrote.
from tpy import Int32, error_return, ReturnException


class MyErr(Exception, ReturnException):
    pass


class OtherErr(Exception, ReturnException):
    pass


@error_return(MyErr)
def fallible(x: Int32) -> Int32:
    if x < 0:
        raise MyErr
    return x * 2


@error_return(MyErr)
def caller(x: Int32) -> Int32:
    try:  # tpyc: error(/tuple form is not supported on a return-tier/)
        return fallible(x)
    except (MyErr, OtherErr):
        return 0


def main() -> None:
    print(caller(3))


main()

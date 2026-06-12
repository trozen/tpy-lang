# Return-tier sibling of exceptions/finally_local_binding: a variable first
# bound inside the finally of a return-tier try/except must hoist (usable in
# the duplicated emissions, visible after the try).
from tpy import Int32, error_return, ReturnException


class MyErr(Exception, ReturnException):
    pass


@error_return(MyErr)
def fallible(x: Int32) -> Int32:
    if x < 0:
        raise MyErr
    return x * 2


def run(x: Int32) -> None:
    try:
        y = fallible(x)
        print(y)
    except MyErr:
        print("err")
    finally:
        note = "done" + ("!" if x < 0 else ".")
        print(note)
    print(note)


def main() -> None:
    run(3)
    run(-1)


main()

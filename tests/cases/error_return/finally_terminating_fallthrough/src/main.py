# The return-tier (goto-dispatched) try/except/finally shares the normal-path
# elision decision, so an always-raising finally must still run on the try
# body's fall-through path.
from tpy import Int32, error_return, ReturnException


class E(Exception, ReturnException):
    pass


@error_return(E)
def may_fail(x: Int32) -> Int32:
    if x < 0:
        raise E
    return x


@error_return(E)
def falls_through() -> Int32:
    try:
        v = may_fail(5)
        print("try body ran, v =", v)
    except E:
        print("unreachable handler")
    finally:
        print("finally ran")
        raise RuntimeError("from finally")
    return 0


@error_return(E)
def returns_from_finally() -> Int32:
    """The finally's return is this tier's only exit on the fall-through path.

    The return tier dispatches through goto + a return slot, so the elided
    fall-through copy left the C++ function falling off its end (UB).
    """
    try:
        v = may_fail(5)
        print("try body ran, v =", v)
    except E:
        print("unreachable handler")
    finally:
        print("finally ran")
        return 7


def call_it() -> None:
    try:
        print(returns_from_finally())
    except E:
        print("caught E")

    # Raises out of call_it, so keep it last.
    try:
        print(falls_through())
    except E:
        print("caught E")


def main() -> None:
    try:
        call_it()
    except RuntimeError:
        print("caught from finally")


main()

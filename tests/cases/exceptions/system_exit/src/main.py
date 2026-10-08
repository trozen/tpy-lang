# sys.exit raises SystemExit, which unwinds like any BaseException: finally
# bodies, with exits and handlers see it, `except Exception` does not catch it,
# and its `code` is the int / str / None it was raised with.
import sys
from typing import Iterator
from tpy import int32, error_return, ReturnException


class Guard:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(self.tag + ": __exit__ sees an exception", exc_val is not None)


class Leave(SystemExit):
    pass


class Shutdown:
    def stop(self, code: int32) -> None:
        sys.exit(code)  # tpyc: ok


class BadInput(Exception, ReturnException):
    pass


# Section finally: a finally body runs before the exit continues.
def finally_runs() -> None:
    try:
        try:
            sys.exit(3)  # tpyc: ok
        finally:
            print("finally: body ran")
    except SystemExit as e:
        print("finally: caught", e.code)


# Section with: a with block's __exit__ sees the exit.
def with_exit() -> None:
    try:
        with Guard("with"):
            sys.exit(4)  # tpyc: ok
    except SystemExit as e:
        print("with: caught", e.code)


def leave_with(code: int32 | str | None) -> None:
    sys.exit(code)  # tpyc: ok


def raise_code(u: int32 | str | None) -> None:
    # The SystemExit constructor's union variant.
    raise SystemExit(u)  # tpyc: ok


def show(tag: str, e: SystemExit) -> None:
    print(tag + ": code", e.code, "str", repr(str(e)))


# Section codes: each code form reaches `code` and str().
def codes() -> None:
    try:
        sys.exit(5)  # tpyc: ok
    except SystemExit as e:
        show("codes int", e)
    try:
        sys.exit("bye")  # tpyc: ok
    except SystemExit as e:
        show("codes str", e)
    try:
        sys.exit()  # tpyc: ok
    except SystemExit as e:
        show("codes exit()", e)
    try:
        sys.exit(None)  # tpyc: ok
    except SystemExit as e:
        show("codes exit(None)", e)
    # SystemExit() and SystemExit(None) both hold None; only str() differs.
    try:
        raise SystemExit()  # tpyc: ok
    except SystemExit as e:
        show("codes SystemExit()", e)
    try:
        raise SystemExit(None)  # tpyc: ok
    except SystemExit as e:
        show("codes SystemExit(None)", e)
    try:
        raise SystemExit(3)  # tpyc: ok
    except SystemExit as e:
        show("codes SystemExit(3)", e)
    k: int = 2
    try:
        # An `int` argument narrows to the int32 code.
        sys.exit(k + 4)  # tpyc: ok
    except SystemExit as e:
        show("codes int arg", e)
    # A union-typed code reaches the matching branch of the union variant.
    try:
        leave_with(None)  # tpyc: ok
    except SystemExit as e:
        show("codes union None", e)
    try:
        leave_with("u")  # tpyc: ok
    except SystemExit as e:
        show("codes union str", e)
    try:
        leave_with(2)  # tpyc: ok
    except SystemExit as e:
        show("codes union int", e)
    try:
        raise_code("w")
    except SystemExit as e:
        show("codes SystemExit(union)", e)
    # A code reassigned before raising is what the handler reads.
    ex = SystemExit(1)
    ex.code = 4  # tpyc: ok
    try:
        raise ex
    except SystemExit as e:
        show("codes reassigned", e)


# Section not_an_exception: `except Exception` lets it through.
def not_an_exception() -> None:
    try:
        try:
            sys.exit(1)  # tpyc: ok
        except Exception:
            print("not_an_exception: WRONG, caught as Exception")
    except BaseException:
        print("not_an_exception: caught as BaseException")


# Section subclass: a SystemExit subclass carries its code.
def subclass() -> None:
    try:
        raise Leave(9)  # tpyc: ok
    except SystemExit as e:
        print("subclass: caught as SystemExit", e.code)
    try:
        raise Leave("why")  # tpyc: ok
    except Leave as e:
        print("subclass: caught as Leave", e.code)


# Section in_method: the exit is raised from a method.
def in_method() -> None:
    try:
        Shutdown().stop(6)  # tpyc: ok
    except SystemExit as e:
        print("in_method: caught", e.code)


def gen() -> Iterator[int32]:
    try:
        yield 1
        sys.exit(8)  # tpyc: ok
        yield 2
    finally:
        print("generator: finally ran")


# Section generator: the exit leaves a generator body, running its finally.
def in_generator() -> None:
    try:
        for x in gen():
            print("generator: got", x)
    except SystemExit as e:
        print("generator: caught", e.code)


# Section error_return: an @error_return body throws the exit; it is not
# returned as the declared error.
@error_return(BadInput)
def checked(n: int32) -> int32:
    if n < 0:
        raise BadInput
    sys.exit(n)  # tpyc: ok
    return n


def in_error_return() -> None:
    try:
        try:
            checked(12)
        except BadInput:
            print("error_return: WRONG, caught as BadInput")
    except SystemExit as e:
        print("error_return: caught", e.code)


# Section swallowed: a return in the finally discards the SystemExit, as in
# CPython.
def swallowed() -> int32:
    try:
        sys.exit(3)  # tpyc: ok
    finally:
        return 7


def main() -> None:
    finally_runs()
    with_exit()
    codes()
    not_an_exception()
    subclass()
    in_method()
    in_generator()
    in_error_return()
    print("swallowed: returned", swallowed())


main()

# Section module level: a top-level statement exits.
try:
    sys.exit(11)  # tpyc: ok
except SystemExit as e:
    print("module level: caught", e.code)

# tpy.dispatch: every same-named variant is its own implementation, resolved
# by argument types and arity; typing.overload keeps CPython's stubs+impl form.
from typing import overload
from tpy import dispatch, error_return, int32, ReturnException


# free function: variants differ by arity
@dispatch
def area(w: int32) -> int32:  # tpyc: ok
    return w * w


@dispatch
def area(w: int32, h: int32) -> int32:
    return w * h


# free function: variants differ by type, with different return types
@dispatch
def tag(x: int32) -> str:  # tpyc: ok
    return "int"


@dispatch
def tag(x: str) -> int32:
    return len(x)


# free function: the fixed-int variant declared AFTER the str one; a literal
# must still reach it under both runtimes (the CPython stub cannot isinstance
# a plain int against int32, so its matcher fits the literal to the slot)
@dispatch
def kind(x: str) -> str:  # tpyc: ok
    return "str"


@dispatch
def kind(x: int32) -> str:
    return "int"


# method: variants on one receiver
class Acc:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    @dispatch
    def add(self, x: int32) -> None:  # tpyc: ok
        self.total += x

    @dispatch
    def add(self, x: str) -> None:
        self.total += len(x)


# reference type: a variant mutates the list it is handed, visible to the caller
@dispatch
def push(xs: list[int32]) -> None:  # tpyc: ok
    xs.append(0)


@dispatch
def push(xs: list[int32], v: int32) -> None:
    xs.append(v)


# typing.overload in the same module keeps its stubs-plus-implementation form
@overload
def show(x: int) -> str: ...
@overload
def show(x: str) -> str: ...
def show(x: int | str) -> str:  # tpyc: ok
    if isinstance(x, int):
        return "n=" + str(x)
    return "s=" + x


class Empty(Exception, ReturnException):
    pass


# free function: variants that carry @error_return keep the handled check
# and the unwrap
@dispatch
@error_return(Empty)
def parse(s: str) -> int:  # tpyc: ok
    if s == "":
        raise Empty()
    return len(s)


@dispatch
@error_return(Empty)
def parse(n: int, scale: int) -> int:
    if n < 0:
        raise Empty()
    return n * scale


@error_return(Empty)
def parse_both() -> int:
    return parse("ab") + parse(-1, 2)  # tpyc: ok


def main() -> None:
    print("free_arity:", area(3), area(2, 5))
    print("free_type:", tag(1), tag("abc"))
    print("free_order:", kind(3), kind("s"))
    a = Acc()
    a.add(2)
    a.add("xyz")
    print("method:", a.total)
    xs: list[int32] = []
    push(xs)
    push(xs, 7)
    print("reference:", xs)
    print("overload:", show(7), show("q"))
    try:
        print("error_return:", parse("abc"), parse(2, 3))
        print(parse(""))
    except Empty:
        print("error_return: caught")
    try:
        parse_both()
    except Empty:
        print("error_return: propagated")


main()

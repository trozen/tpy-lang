# A null Ptr[T] coerced to T (argument, return, initializer, value-union
# argument) raises AttributeError at the coercion, as an explicit deref does.
from typing import Iterator

from tpy import Ptr, int32


class Rec:
    def __init__(self, x: int32) -> None:
        self.x = x


def take(x: int32) -> int32:
    return x + 1


def take_rec(r: Rec) -> int32:
    return r.x


def take_union(x: bool | str) -> str:
    return "union"


def ret(p: Ptr[int32]) -> int32:
    return p  # tpyc: ok


class User:
    def run(self, p: Ptr[int32]) -> int32:
        # method: argument coercion inside a method body
        return take(p)  # tpyc: ok


def gen(p: Ptr[int32]) -> Iterator[int32]:
    yield 1
    # generator: argument coercion after a yield
    yield take(p)  # tpyc: ok


def main() -> None:
    p = Ptr[int32]()
    q = Ptr[Rec]()
    b = Ptr[bool]()
    try:
        v = take(p)  # tpyc: ok
        print("arg", v)
    except AttributeError as e:
        print("arg", e)
    try:
        v = ret(p)
        print("return", v)
    except AttributeError as e:
        print("return", e)
    try:
        x: int32 = p  # tpyc: ok
        print("init", x)
    except AttributeError as e:
        print("init", e)
    try:
        v = take_rec(q)  # tpyc: ok
        print("record", v)
    except AttributeError as e:
        print("record", e)
    try:
        s = take_union(b)  # tpyc: ok
        print("union", s)
    except AttributeError as e:
        print("union", e)
    try:
        v = User().run(p)
        print("method", v)
    except AttributeError as e:
        print("method", e)
    out: list[int32] = []
    try:
        for v in gen(p):
            out.append(v)
    except AttributeError as e:
        print("generator", out, e)


main()

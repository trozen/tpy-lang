# A ValueType-bounded `T` renders as the function written for the concrete
# value type: `T | None` returns, frames, `Own[T]`, prints; reassigned
# parameters in part_reassign, None tests, match and varargs in part_optional.
# No module-level `r = via(s)` section: it is refused
# (BUGS.md#bounded-optional-result-owned-str-local-rejects).
import asyncio
from typing import Iterable, Iterator
from tpy import Own, ValueType, int32
import part_optional
import part_reassign


# free function: its own `T | None` parameter returned whole
def fopt[T: ValueType](x: T | None) -> T | None:
    return x  # tpyc: ok


# method-own bound in a class whose own parameter is unbounded
class Holder[U]:
    def __init__(self, u: U) -> None:
        # the copy is real at a reference `U`
        self.u = u  # tpyc: warning(/may copy U into field/)

    def tag[T: ValueType](self, x: T) -> T | None:
        return x  # tpyc: ok


# class-bounded method: a plain `T` parameter into a `T | None` return
class C[T: ValueType]:
    def __init__(self) -> None:
        pass

    def opt(self, x: T) -> T | None:
        return x  # tpyc: ok


# getter of a bounded field, called through a holder the caller does not mutate
class Cell[T: ValueType]:
    def __init__(self, v: T) -> None:
        self.v = v

    def get(self) -> T:
        return self.v


def read_cell(c: Cell[str]) -> str:
    return c.get()  # tpyc: ok


# generator: the frame holds its own copy of the parameter
def gen[T: ValueType](x: T) -> Iterator[T]:
    yield x
    yield x  # tpyc: ok


# async: the parameter is read after the suspension point
async def aw[T: ValueType](x: T) -> T:
    await asyncio.sleep(0)
    return x  # tpyc: ok


# generator over an `Iterable[Own[T]]` parameter, consumed in the body
def drain[T: ValueType](xs: Iterable[Own[T]]) -> Iterator[T]:
    for x in xs:  # tpyc: ok
        yield x


# async over an `Iterable[Own[T]]` parameter, consumed in the body
async def adrain[T: ValueType](xs: Iterable[Own[T]]) -> int32:
    n = 0
    for x in xs:  # tpyc: ok
        await asyncio.sleep(0)
        print("adrain", x)
        n += 1
    return n


# closure capture of a bounded parameter
def cap[T: ValueType](x: T) -> None:
    def inner() -> None:
        print("cap", x)  # tpyc: ok
    inner()


# a bare `T` printed at a tuple instantiation, an unbounded one at a list
def show_t[T: ValueType](x: T) -> None:
    print("show_t", x)  # tpyc: ok


def show_any[T](x: T) -> None:
    print("show_any", x)  # tpyc: ok


# unbounded `T` returned into `Own[T]`: the copy is real at a reference `T`
def ret_own[T](v: T) -> Own[T]:
    return v  # tpyc: warning(/may copy T into owned storage/)


# an `Own[T]` parameter into a `T | None` return
def opt_back[T: ValueType](x: Own[T]) -> T | None:
    return x  # tpyc: ok


async def amain() -> None:
    print("async", await aw(7), await aw("as"))
    print("adrain", await adrain([1, 2]), await adrain(["p", "q"]))


def main() -> None:
    print("fopt", fopt(5), fopt("s"), fopt(b"b"))
    print("tag", Holder(1).tag(3), Holder(1).tag("t"))
    print("opt", C[int32]().opt(4), C[str]().opt("o"))
    print("getter", read_cell(Cell("g")))
    for v in gen("gs"):
        print("gen", v)
    for n in gen(3):
        print("gen", n)
    for d in drain([10, 20]):
        print("drain", d)
    for e in drain(["e1", "e2"]):
        print("drain", e)
    cap(9)
    cap("c")
    print("ret_own", ret_own("r"))
    print("opt_back", opt_back(6), opt_back("ob"))
    t: tuple[int32, str] = (1, "a")
    show_t(t)
    xs = [1, 2]
    show_any(xs)
    asyncio.run(amain())
    part_reassign.main()
    part_optional.main()


main()

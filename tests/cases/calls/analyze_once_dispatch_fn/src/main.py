# A @dispatch function's argument is analyzed once: a consuming call
# inside it, under every carrier and at every position, is consumed once
# and the winner receives what the overload probe analyzed.
import asyncio
from typing import Iterator, Self
from tpy import Own, dispatch, int32


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self


def plain(x: Own[B]) -> Own[B]:
    return x


def ident[T](x: Own[T]) -> Own[T]:
    return x


@dispatch
def f(x: B) -> str:
    x.v += 100
    return f"B {x.v}"


@dispatch
def f(x: int32) -> str:
    return f"int32 {x}"


@dispatch
def f(x: str) -> str:
    return f"str {x}"


@dispatch
def f(x: list[int32]) -> str:
    return f"list {x[0]} {len(x)}"


@dispatch
def f(x: dict[str, int32]) -> str:
    return f"dict {x['k']}"


@dispatch
def f(x: set[int32]) -> str:
    return f"set {len(x)}"


@dispatch
def f(x: tuple[int32, str]) -> str:
    return f"tuple {x[0]} {x[1]}"


async def co(b: Own[B]) -> int32:  # tpyc: warning(/Own\[B\] param 'b' is never consumed/)
    return b.v


def carriers() -> None:
    flag = True
    # name: the argument aliases the local, so the callee's write shows.
    a = B(1)
    print("name:", f(a), a.v)
    # the consuming call itself
    print("call:", f(B(2).take()))
    b = B(3)
    print("field:", f(b.take().v))  # tpyc: ok
    b = B(4)
    print("binop:", f(b.take().v + 1))  # tpyc: ok
    b = B(5)
    print("walrus:", f((y := b.take().v)))  # tpyc: ok
    print("walrus-target:", y)
    b = B(6)
    print("select-cond:", f("a" if b.take().v > 0 else "b"))  # tpyc: ok
    b = B(7)
    print("select-arm:", f(f"{b.take().v}" if flag else "none"))  # tpyc: ok
    b = B(8)
    print("fstring:", f(f"{b.take().v}"))  # tpyc: ok
    b = B(9)
    print("list:", f([b.take().v]))  # tpyc: ok
    b = B(10)
    print("dict:", f({"k": b.take().v}))  # tpyc: ok
    b = B(11)
    print("set:", f({b.take().v}))  # tpyc: ok
    b = B(12)
    print("comprehension:", f([x for x in [b.take().v]]))  # tpyc: ok
    b = B(13)
    print("tuple:", f((b.take().v, "s")))  # tpyc: ok
    # the callee mutates the object the nested call hands back
    b = B(14)
    print("nested-plain:", f(plain(b.take())))  # tpyc: ok
    b = B(15)
    print("nested-generic:", f(ident(b.take())))  # tpyc: ok


class Runner:
    def run(self) -> None:
        # method position
        b = B(20)
        print("method:", f(b.take().v))  # tpyc: ok


def gen() -> Iterator[str]:
    # generator position
    b = B(30)
    yield f(b.take().v)  # tpyc: ok


async def run_async() -> None:
    # async position, and an awaited consuming call as the argument
    b = B(40)
    print("async:", f(b.take().v))  # tpyc: ok
    c = B(41)
    print("await:", f(await co(c.take())))  # tpyc: ok


def comprehension() -> None:
    # comprehension position: one consume per element
    print("comprehension-position:", [f(B(i).take().v) for i in range(50, 52)])  # tpyc: ok


def main() -> None:
    carriers()
    Runner().run()
    for s in gen():
        print("generator:", s)
    asyncio.run(run_async())
    comprehension()


main()

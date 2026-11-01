# A narrowed storage-Optional binding (`list[R | None]` loop variable, comprehension
# target, owned step, `Own[R | None]` parameter) reads as its payload, aliased in place.
import asyncio
from typing import Iterable, Iterator, Protocol
from tpy import Own, int32, readonly


class R:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def val(self) -> int32:
        return self.n

    def bump(self) -> None:
        self.n += 1

    def __repr__(self) -> str:
        return f"R({self.n})"


class Value(Protocol):
    def val(self) -> int32: ...


def plain(v: Value) -> int32:
    return v.val()


def twin_r(v: R) -> int32:
    return v.n


def opt_param(v: R | None) -> int32:
    return -1 if v is None else v.n


def gen(v: Value) -> Iterator[int32]:
    yield v.val()
    yield v.val()


async def coro(v: Value) -> int32:
    return v.val()


# Free function loop.
def free_loop() -> None:
    xs: list[R | None] = [R(1), None]
    ys: list[R] = []
    for x in xs:
        if x is not None:
            print("free plain", plain(x))  # tpyc: ok
            print("free twin", twin_r(x))  # tpyc: ok
            x.bump()  # tpyc: ok
            y = x  # tpyc: ok
            y.n = 9
            print("free alias", x.n, x is y)
            print("free text", x, f"{x}", repr(x))  # tpyc: ok
            print("free opt_param", opt_param(x))  # tpyc: ok
            t: tuple[R | None, int32] = (x, 0)  # tpyc: ok
            p, k = t
            if p is not None:
                p.n = 11
            print("free tuple", x.n, k)
            # Warned copy: x is read again below (match), so ys gets its own R.
            ys.append(x)  # tpyc: warning(/copies/)
            match x:  # tpyc: ok
                case R(n=n):
                    print("free match", n)
    ys[0].n = 4
    print("free append copy", ys[0].n)
    src: list[list[int32] | None] = [[1], None]
    for o in src:
        if o is not None:
            print("free len", len(o))  # tpyc: ok
            a = o  # tpyc: ok
            a.append(1)
            print("free len alias", len(o))


# Method body loop.
class Holder:
    xs: list[R | None]

    def __init__(self) -> None:
        self.xs = [R(2), None]

    def walk(self) -> None:
        for x in self.xs:
            if x is not None:
                print("method plain", plain(x))  # tpyc: ok
                y = x  # tpyc: ok
                y.n = 7
                print("method alias", x.n)


# List comprehension target.
def comprehension() -> None:
    xs: list[R | None] = [R(3), None, R(4)]
    print("comp plain", [plain(x) for x in xs if x is not None])  # tpyc: ok
    print("comp twin", [twin_r(x) for x in xs if x is not None])  # tpyc: ok
    # Warned copy, a missed move: xs is dead here (TODO.md consuming comprehension).
    kept = [x for x in xs if x is not None]  # tpyc: warning(/copies/)
    kept[0].n = 30
    print("comp kept copy", kept[0].n, len(kept))


# Owned optional step.
def owned_step(src: Iterable[Own[R | None]]) -> None:
    for x in src:
        if x is not None:
            print("owned plain", plain(x))  # tpyc: ok
            y = x  # tpyc: ok
            y.n = 50
            print("owned alias", x.n)


# Async body: a loop without `await` keeps the storage binding.
async def async_body(xs: list[R | None]) -> None:
    for x in xs:
        if x is not None:
            print("async plain", plain(x))  # tpyc: ok
            y = x  # tpyc: ok
            y.n = 60
            print("async alias", x.n)
    # A loop that suspends binds the borrow form; its read is unchanged.
    for z in xs:
        if z is not None:
            print("async suspend", await coro(z))  # tpyc: ok


# `try` body.
def try_body() -> None:
    xs: list[R | None] = [R(5), None]
    for x in xs:
        try:
            if x is not None:
                print("try plain", plain(x))  # tpyc: ok
                y = x  # tpyc: ok
                y.n = 70
                print("try alias", x.n)
        finally:
            print("try finally")


# Generator callee iterated in place; CPython's frame sees the later write.
def frame_callee() -> None:
    xs: list[R | None] = [R(6), None]
    for x in xs:
        if x is not None:
            for s in gen(x):  # tpyc: ok
                print("frame", s)
                x.n = 9


# Coroutine built from a sync loop's storage binding.
def coro_from_loop() -> None:
    xs: list[R | None] = [R(12), None]
    for x in xs:
        if x is not None:
            print("coro", asyncio.run(coro(x)))  # tpyc: ok
            x.n = 13
            print("coro after", asyncio.run(coro(x)))


# Const loop variable over a readonly list: the alias is const.
def readonly_loop(xs: readonly[list[R | None]]) -> None:
    for x in xs:
        if x is not None:
            y = x  # tpyc: ok
            print("readonly alias", y.n, x is y)


# `Own[R | None]` parameter: the same payload read.
def own_param(o: Own[R | None], other: R | None, p: R) -> Own[R | None]:
    if o is not None:
        o.bump()  # tpyc: ok
        print("own field", o.n)  # tpyc: ok
        y = o  # tpyc: ok
        y.n = 20
        print("own alias", o.n, plain(o))
        print("own is", o is other, o is p, other is o, o is y)  # tpyc: ok
    return o


# Comprehension unpack head: the Optional element binds its storage in place.
def comp_unpack() -> None:
    pairs: list[tuple[int32, R | None]] = [(1, R(21)), (2, None)]
    print("unpack plain", [plain(x) for (i, x) in pairs if x is not None])  # tpyc: ok
    print("unpack whole", [opt_param(x) + i for (i, x) in pairs])  # tpyc: ok


def main() -> None:
    free_loop()
    Holder().walk()
    comprehension()
    oxs: list[R | None] = [R(8), None]
    owned_step(oxs)
    axs: list[R | None] = [R(10), None]
    asyncio.run(async_body(axs))
    try_body()
    frame_callee()
    coro_from_loop()
    rxs: list[R | None] = [R(14), None]
    readonly_loop(rxs)
    comp_unpack()
    p = R(16)
    kept = own_param(R(15), None, p)
    if kept is not None:
        print("own kept", kept.n)


main()

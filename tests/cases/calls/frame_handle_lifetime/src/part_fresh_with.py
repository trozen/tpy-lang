# A `with` target that borrows an element (or a field of one) of a FRESH
# container and is read after the block, or across a `yield`: the container
# is held in an enclosing slot (a frame slot in a generator) until then.
import asyncio
from typing import Iterator, Self

from tpy import Own, Ptr, int32, nocopy


@nocopy
class CM:
    name: str

    def __init__(self, n: int32) -> None:
        self.name = "cm-long-enough-to-heap-" + str(n) * 10

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a: None, b: None, c: None) -> None:
        self.name += "!"


@nocopy
class Mid:
    cm: CM

    def __init__(self, n: int32) -> None:
        self.cm = CM(n)


@nocopy
class Bag:
    items: list[Mid]

    def __init__(self, n: int32) -> None:
        self.items = [Mid(n)]

    def __getitem__(self, i: int32) -> Mid:
        return self.items[i]


@nocopy
class CMBag:
    items: list[CM]

    def __init__(self, n: int32) -> None:
        self.items = [CM(n)]

    def __getitem__(self, i: int32) -> CM:
        return self.items[i]


def mk_mids(n: int32) -> Own[list[Mid]]:
    return [Mid(n)]


class Relay:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    def pass_cm(self, c: CM) -> CM:
        return c


def sync_after() -> None:
    # A lent element, a field of one, and a field of a builtin element.
    r = Relay()
    with r.pass_cm(CMBag(1)[0]) as a1:  # tpyc: ok
        pass
    junk1 = [str(k) * 40 for k in range(20)]
    print("with_elem", a1.name[0:14], a1.name[-1], len(junk1))
    with r.pass_cm(Bag(2)[0].cm) as a2:  # tpyc: ok
        pass
    junk2 = [str(k) * 40 for k in range(20)]
    print("with_field", a2.name[0:14], a2.name[-1], len(junk2))
    with r.pass_cm(mk_mids(3)[0].cm) as a3:  # tpyc: ok
        pass
    junk3 = [str(k) * 40 for k in range(20)]
    print("with_list_field", a3.name[0:14], a3.name[-1], len(junk3))


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Src:
    box: Box

    def __init__(self, n: int32) -> None:
        self.box = Box(n)

    def __getitem__(self, i: int32) -> Box:
        return self.box


class Mgr:
    p: Ptr[Box]
    out: Box

    def __init__(self, b: Box) -> None:
        b.n += 1
        self.p = b
        self.out = Box(42)

    def __enter__(self) -> Box:
        return self.out

    def __exit__(self, a: None, b: None, c: None) -> None:
        pass


def stored_ptr() -> None:
    # A manager keeps the address of a lent element of a fresh container,
    # but the target it hands back is its own field: nothing the target
    # reads after the block points into the dead container.
    with Mgr(Src(6)[0]) as b:  # tpyc: ok
        pass
    print("with_stored_ptr", b.n)


def gen_stored_ptr() -> Iterator[int32]:
    # ... in a generator body, the target read across a yield after the block.
    for k in range(2):
        with Mgr(Src(k)[0]) as b:  # tpyc: ok
            pass
        yield b.n


async def coro_after(k: int32) -> None:
    # A lent element's target in a coroutine body, read after an await
    # that follows the block.
    r = Relay()
    with r.pass_cm(CMBag(k)[0]) as a:  # tpyc: ok
        pass
    await asyncio.sleep(0)
    junk = [str(i) * 40 for i in range(20)]
    print("with_async", a.name[0:14], a.name[-1], len(junk))


async def coro_stored_ptr() -> None:
    # ... and in a coroutine body, across an await after the block.
    for k in range(2):
        with Mgr(Src(k)[0]) as b:  # tpyc: ok
            await asyncio.sleep(0)
        await asyncio.sleep(0)
        print("with_stored_ptr_async", b.n)


def gen_after(k: int32) -> Iterator[str]:
    # The target read across a suspension after the block.
    r = Relay()
    with r.pass_cm(CMBag(k)[0]) as a:  # tpyc: ok
        pass
    yield a.name[0:14]
    junk = [str(i) * 40 for i in range(20)]
    yield a.name[-1] + str(len(junk))


def main() -> None:
    sync_after()
    stored_ptr()
    for n in gen_stored_ptr():
        print("with_stored_ptr_gen", n)
    asyncio.run(coro_stored_ptr())
    asyncio.run(coro_after(5))
    for s in gen_after(4):
        print("with_gen", s)

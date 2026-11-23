# A generator body whose loop head hands a nested temporary to a generator
# call: the frame holds it across the yields of its pass, one slot per pass.
# A view no operand stores and a borrow tuple's element need no holder.
import asyncio
from typing import Callable, Final, Iterator, Sized

from tpy import Span, StrView, ValueType, int32, nocopy

GREETING: Final[str] = "hello-final"
NAMES: list[str] = ["alpha", "beta"]
NUMS: list[int32] = [1, 2, 3]


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def ident(c: Cell) -> Cell:
    return c


def inner(c: Cell) -> Iterator[int32]:
    yield c.n
    c.n += 1
    yield c.n


def outer() -> Iterator[int32]:
    for i in range(3):
        for n in inner(ident(Cell(i * 10))):  # tpyc: ok
            yield n


def greeting() -> StrView:
    return GREETING


def name_of(i: int32) -> StrView:
    return NAMES[i]


def nums() -> Span[int32]:
    return NUMS


def sv_chars(s: StrView) -> Iterator[str]:
    for c in s:
        yield c


def gen_span(s: Span[int32]) -> Iterator[int32]:
    for x in s:
        yield x


async def co_len(s: StrView) -> int32:
    await asyncio.sleep(0.0)
    return len(s)


async def task_len() -> int32:
    t = asyncio.create_task(co_len(greeting()))  # tpyc: ok
    return await t


def unnamed_views(fn: Callable[[], StrView]) -> None:
    # A view a call returns with no operand to point into: durable at every
    # position -- a loop head, a bound handle, a task, an await.
    for c in sv_chars(greeting()):  # tpyc: ok
        print("unnamed_head", c)
        break
    h = sv_chars(greeting())  # tpyc: ok
    print("unnamed_handle", "".join(h))
    print("unnamed_task", asyncio.run(task_len()))
    print("unnamed_await", asyncio.run(co_len(greeting())))  # tpyc: ok
    # ... a scalar operand, a Span with none, a callable value with none.
    print("unnamed_scalar", "".join(sv_chars(name_of(1))))  # tpyc: ok
    print("unnamed_span", sum(gen_span(nums())))  # tpyc: ok
    print("unnamed_callable", "".join(sv_chars(fn())))  # tpyc: ok


def pick_t(c: Cell) -> tuple[Cell, int32]:
    return (c, 1)


def borrow_tuple_element() -> None:
    # A borrow tuple's element names its object, which the frame holds:
    # nothing warns.
    h = inner(pick_t(Cell(3))[0])  # tpyc: ok
    print("btuple_elem", list(h))


def gen_len(v: Sized) -> Iterator[int32]:
    yield len(v)


def span_local() -> None:
    # A Span local handed to a generator: the frame copies the handle,
    # whose list outlives it; a later rebind of the local is not seen.
    xs = [1, 2, 3]
    ys = [5, 6, 7, 8]
    sp: Span[int32] = xs
    g = gen_len(sp)  # tpyc: ok
    h = gen_span(sp)  # tpyc: ok
    sp = ys
    print("span_local", list(g), sum(h), len(sp))


class VPad(ValueType):
    s: str

    def __init__(self, n: int32) -> None:
        self.s = "vpad" + str(n) * 20

    def trimmed(self) -> StrView:
        return self.s


class KeyEcho:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag

    def __getitem__(self, k: StrView) -> StrView:
        return k


def sv_same(s: StrView) -> StrView:
    return s


def value_type_and_key_views() -> None:
    # A view of a fresh value-type record's buffer at a loop head: the
    # head holds the record, through a field and through a method.
    for c in sv_same(VPad(4).s):  # tpyc: ok
        print("vpad_field", c)
        break
    for c in sv_same(VPad(5).trimmed()):  # tpyc: ok
        print("vpad_method", c)
        break
    # A `__getitem__` viewing its key, over a fresh key's view: the key's
    # string is held for the call.
    echo = KeyEcho(0)
    print("key_view", "".join(sv_chars(echo[sv_same(GREETING + "!")])))  # tpyc: ok


def vpad_pair(n: int32) -> tuple[VPad, int32]:
    return (VPad(n), n)


def tuple_value_element() -> None:
    # A view of a value element of a fresh tuple: nothing holds the tuple,
    # so the bound handle warns (never advanced here).
    h = sv_chars(vpad_pair(3)[0].s)  # tpyc: warning(/Result borrows from temporary argument/)
    print("tuple_value_element warned")


def main() -> None:
    print("generator", list(outer()))
    span_local()
    value_type_and_key_views()
    tuple_value_element()
    unnamed_views(greeting)
    borrow_tuple_element()

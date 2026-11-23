# A slice of a fresh value handed to an awaited coroutine: the frame holds the
# sliced-from value across the `await`, so the awaited frame's view stays live.
import asyncio
from typing import Callable, Iterable, Iterator

from tpy import BytesView, Own, Span, StrView, String, char, int32


def make(n: int32) -> str:
    return "abcdefghijklmnopqrstuvwxyz0123456789" * n


def make_bytes(n: int32) -> bytes:
    return b"0123456789abcdef" * n


def make_list(n: int32) -> Own[list[int32]]:
    out: list[int32] = []
    for i in range(n):
        out.append(i * 10)
    return out


async def collect[T](it: Iterable[T]) -> str:
    await asyncio.sleep(0)
    # Fresh allocations between the suspension and the read reuse any
    # storage the argument had freed.
    junk = [make(3) for _ in range(6)]
    out = ""
    for c in it:
        out += str(c)
    return out + "/" + str(len(junk))


async def co_str(s: str) -> str:
    await asyncio.sleep(0)
    return s


async def body(n: int32) -> None:
    print("slice", await collect(make(n)[2:12]))  # tpyc: ok
    # A slice under a further view: the frame holds the sliced-from string,
    # the views over it are values.
    print("slice_strip", await collect(f"  {n}abc  "[1:].strip()))  # tpyc: ok
    print("slice_bytes", await collect(make_bytes(n)[2:6]))  # tpyc: ok
    print("slice_list", await collect(make_list(n + 3)[1:3]))  # tpyc: ok
    # A `str` parameter owns its argument: the slice is copied, no holder.
    print("slice_owned", await co_str(make(n)[1:5]))  # tpyc: ok
    # A slice of a `str` literal views static storage: no holder.
    print("slice_literal", await collect("hello world"[2:7]))  # tpyc: ok
    # ... and so does a slice of a `bytes` literal.
    print("slice_bytes_literal", await collect(b"hello world"[2:7]))  # tpyc: ok
    # A `bytes` literal a view method reads: its static storage, no holder.
    print("strip_bytes_literal",
          await collect(b"  hello-bytes-literal  ".strip()))  # tpyc: ok


def views(s: StrView) -> Iterator[StrView]:
    yield s


def bviews(b: BytesView) -> Iterator[BytesView]:
    yield b


def sync_head() -> None:
    # An ordinary function's loop head keeps the string its slice views
    # until the loop ends.
    out: list[str] = []
    for i in range(2):
        for w in views(make(i + 1)[i + 1:].strip()):  # tpyc: ok
            out.append(str(w[0:3]))
    print("slice_sync_head", out)
    for b in bviews(b"  hello-bytes-literal  ".strip()):  # tpyc: ok
        junk = [make(3) for _ in range(6)]
        print("strip_bytes_sync_head", b, len(junk))


def span_gen(s: Span[int32]) -> Iterator[int32]:
    yield s[0]
    yield s[1]


def slice_of_param(xs: list[int32]) -> None:
    # A slice of a parameter views storage that outlives the loop: passed
    # in place, as at a plain call. The read-only face does not build
    # (BUGS.md#readonly-list-slice-span-mutable).
    xs[0] = xs[0]
    for v in span_gen(xs[1:4]):  # tpyc: ok
        print("slice_param", v)


class Pad:
    s: str

    def __init__(self, n: int32) -> None:
        self.s = "  pad" + str(n) * 20 + "  "

    def me(self) -> "Pad":
        return self


def each_pad(p: Pad) -> Iterator[str]:
    yield str(p.s.strip())


def method_steps() -> None:
    # A view under a method step and a field of a fresh object: the head
    # holds the object.
    for w in views(Pad(3).me().s.strip()):  # tpyc: ok
        print("step_strip", w)
    # A method result pointing into a fresh object, at a generator head.
    for t in each_pad(Pad(4).me()):  # tpyc: ok
        print("step_head", t)
    # A handle over a lent part of a fresh object: held, and nothing warns.
    g = each_pad(Pad(5).me())  # tpyc: ok
    junk = [make(3) for _ in range(6)]
    # Joined, not `list(g)`: BUGS.md#runtime-collect-adl-user-coroutine.
    print("step_handle", "".join(g), len(junk))


def pass_view(s: StrView) -> StrView:
    return s


def via_callable(fn: Callable[[StrView], StrView]) -> None:
    # A view a callable value returns may point into its argument: the
    # fresh argument is held, at a bound handle and at a loop head.
    it = views(fn(make(1)))  # tpyc: ok
    junk = [make(3) for _ in range(6)]
    for v in it:
        print("callable_handle", str(v)[0:6], len(junk))
    for w in views(fn(make(2))):  # tpyc: ok
        print("callable_head", str(w)[0:6])


def chars(xs: Iterable[char]) -> Iterator[char]:
    for x in xs:
        yield x


def gen_fresh_slice() -> Iterator[str]:
    # A slice of a fresh string at a structural parameter a generator head
    # keeps: the frame holds the sliced-from string.
    for c in chars(make(1)[2:8]):  # tpyc: ok
        yield str(c)


def make_identity() -> Callable[[StrView], StrView]:
    return pass_view


def view_of_string(s: String) -> StrView:
    return s


def owned_of_view() -> Iterator[str]:
    # A view of a fresh object copied into an owned `String` argument: the
    # copy is the frame's own, held beside the object.
    for c in view_of_string(Pad(6).me().s.strip()):  # tpyc: ok
        yield str(c)


def callable_expression() -> None:
    # A callable reached through an expression viewing a literal: static
    # storage, passed in place.
    for w in views(make_identity()("literal-view")):  # tpyc: ok
        print("callable_expr", w)


class CharCell:
    c: char

    def __init__(self, c: char) -> None:
        self.c = c


class CharBox:
    cell: CharCell

    def __init__(self, c: char) -> None:
        self.cell = CharCell(c)

    @property
    def inner(self) -> CharCell:
        return self.cell


def view_of_char(s: StrView) -> StrView:
    return s


def char_view() -> None:
    # A `char` viewed as a string views static storage, nothing of the
    # fresh object (read through a property) it was read from.
    v = view_of_char(CharBox("x").inner.c)  # tpyc: ok
    print("char_view", v)


def select_of_fields(f: bool) -> None:
    # A select of fields of fresh objects at a bound handle: each arm holds
    # its object, and nothing warns.
    it = views(Pad(7).s if f else Pad(8).s)  # tpyc: ok
    junk = [make(3) for _ in range(6)]
    for w in it:
        print("select_fields", str(w)[0:6], len(junk))


def main() -> None:
    asyncio.run(body(2))
    sync_head()
    slice_of_param([1, 2, 3, 4, 5])
    method_steps()
    via_callable(pass_view)
    print("owned_of_view", "".join(owned_of_view()))
    callable_expression()
    print("gen_fresh_slice", "".join(gen_fresh_slice()))
    select_of_fields(False)
    char_view()

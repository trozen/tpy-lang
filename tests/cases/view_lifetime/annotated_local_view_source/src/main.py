# An ANNOTATED owned-family local (x: str / x: bytes) fed a view-safe source
# and never mutated resolves as a VIEW: the annotation's view->owned coerce is
# stale and must render bare (was: string_view/span bound to a materialized
# owned temporary dying at end of statement -- silent UAF). Covers the plain,
# branch-hoisted, and generator-frame binding shapes. Mutated locals and
# owned returns still materialize (the inverse). The generated-code snapshot
# is the regression guard: the dangle often prints correctly off dead memory.
# The tuple-literal sections are the inverse at a call lending from a
# temporary argument: the local copies, as the inferred form does.
import asyncio
from typing import Callable, Iterator

from tpy import BytesView, StrView, int32


def view_from_param(sv: StrView) -> None:
    label: str = sv  # tpyc: ok
    print(label, len(label))


def view_from_borrowing_call(s: str) -> None:
    t: str = s.strip()  # tpyc: ok
    print(t, len(t))


def bytes_view_from_param(bv: BytesView) -> None:
    label: bytes = bv  # tpyc: ok
    print(label, len(label))


def view_reassigned_from_view(sv: StrView, sv2: StrView) -> None:
    label: str = sv
    label = sv2  # tpyc: ok
    print(label)


def mutated_stays_owned(sv: StrView) -> str:
    m: str = sv
    m += "!"
    return m


def owned_return_still_copies(sv: StrView) -> str:
    return sv  # tpyc: ok


def hoisted_branch(sv: StrView, flag: bool) -> None:
    if flag:
        label: str = sv  # tpyc: ok
    else:
        label = "other"
    print(label)


def gen_frame(sv: StrView) -> Iterator[int]:
    label: str = sv  # tpyc: ok
    print(label)
    yield len(label)


def first_of(p: tuple[str, int32]) -> StrView:
    return p[0]


class Picker:
    def first_of(self, p: tuple[str, int32]) -> StrView:
        return p[0]


# free function: the tuple literal dies at the end of the statement.
def tuple_temp_free(s: str) -> None:
    v: str = first_of((s, 1))  # tpyc: ok
    w: str = first_of((s + "x", 1))  # tpyc: ok
    print("tuple free", v, w)


# method
def tuple_temp_method(s: str) -> None:
    v: str = Picker().first_of((s, 1))  # tpyc: ok
    print("tuple method", v)


# Callable value
def tuple_temp_callable(s: str, f: Callable[[tuple[str, int32]], StrView]) -> None:
    v: str = f((s, 1))  # tpyc: ok
    print("tuple callable", v)


# generator frame
def tuple_temp_gen(s: str) -> Iterator[str]:
    v: str = first_of((s, 1))  # tpyc: ok
    yield v


# async frame
async def tuple_temp_async(s: str) -> str:
    v: str = first_of((s, 1))  # tpyc: ok
    await asyncio.sleep(0)
    return v


def main() -> None:
    long = "this is long enough to dodge the small-string buffer"
    tuple_temp_free(long)
    tuple_temp_method(long)
    tuple_temp_callable(long, first_of)
    for v in tuple_temp_gen(long):
        print("tuple gen", v)
    print("tuple async", asyncio.run(tuple_temp_async(long)))
    view_from_param("hello")
    view_from_borrowing_call("  hi  ")
    bytes_view_from_param(b"abc")
    view_reassigned_from_view("one", "two")
    print(mutated_stays_owned("own"))
    print(owned_return_still_copies("ret"))
    hoisted_branch("first", True)
    hoisted_branch("first", False)
    for n in gen_frame("frame"):
        print(n)


main()

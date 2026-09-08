# Narrowed value-Optional NAMES at resumable-frame sinks deref to the
# inner value: frame reassign, yield (param and loop-var), whole-optional
# yield inverse, module global. Field faces live in
# narrowed_value_opt_field_yield.
from tpy import Int32
from typing import Iterator

G: Int32 | None = 5


def g(p: Int32 | None) -> Iterator[Int32]:
    q = 0
    if p is not None:
        q = p
    yield q
    if p is not None:
        yield p
    yield -1


def g_loop(d: dict[str, Int32 | None]) -> Iterator[Int32]:
    for val in d.values():
        if val is not None:
            yield val


def g_whole(p: Int32 | None) -> Iterator[Int32 | None]:
    if p is not None:
        yield p
    yield None


def g_frame_whole(p: Int32 | None) -> Iterator[Int32]:
    q2: Int32 | None = None
    if p is not None:
        q2 = p
    yield 0
    if q2 is not None:
        yield q2
    yield -2


def g_global() -> Iterator[Int32]:
    # A module global is read through its slot rather than the frame, and the
    # guard still reaches the first yield; a re-read after that yield is the
    # reject error_narrowed_global_stale_yield pins.
    if G is not None:
        yield G
    yield -3


def g_view(s: str | None) -> Iterator[str]:
    if s is not None:
        yield s
    yield "end"


def main() -> None:
    for x in g(4):
        print(x)
    d: dict[str, Int32 | None] = {"a": 1, "b": None, "c": 2}
    for x in g_loop(d):
        print(x)
    for w in g_whole(6):
        if w is not None:
            print(w)
        else:
            print("none")
    for x in g_frame_whole(8):
        print(x)
    for x in g_global():
        print("global", x)
    for s in g_view("v"):
        print(s)


main()

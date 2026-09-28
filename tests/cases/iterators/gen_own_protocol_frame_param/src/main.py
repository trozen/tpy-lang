# An `Own[<static protocol>]` param on a resumable body: the frame captures the
# moved-in conformer by value, so the body is admitted at the frame-param gate.
import asyncio
from tpy import Own, int32
from typing import Iterable, Iterator


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


# free generic generator: the admitted `Own[static P]` frame param.
def each[T](items: Own[Iterable[T]]) -> Iterator[tuple[int32, T]]:  # tpyc: warning(/never consumed/)
    i: int32 = 0
    for item in items:
        yield (i, item)
        yield (i, item)
        i += 1


# monomorphic twin: the same body at a concrete element type. NOT called -- its
# call site still rejects at the untouched call-arg gate
# (call.arg_shape.own_protocol.static), so only the body is exercised here.
def each_mono(items: Own[Iterable[int32]]) -> Iterator[tuple[int32, int32]]:  # tpyc: warning(/never consumed/)
    i: int32 = 0
    for item in items:
        yield (i, item)
        yield (i, item)
        i += 1


# async body with the same param: the frame-param gate admits it too. NOT
# awaited -- every drive shape for such a coroutine still rejects elsewhere
# (inline await at res.await_param_type:own_protocol.static; any call of it,
# create_task / asyncio.run included, at call.arg_shape.own_protocol.static).
async def count_mono(items: Own[Iterable[int32]]) -> int32:  # tpyc: warning(/never consumed/)
    s: int32 = 0
    for _item in items:
        s += 1
    return s


def reference_element() -> None:
    pts = [Point(1), Point(2)]
    seen: int32 = 0
    # The bare-`T` yield slot hands a reference element out BY VALUE on both
    # emit paths (the generic-yield copy tracked in BUGS.md), so the witness
    # yields `tuple[int32, T]`, whose slot is pointer-formed and does alias:
    # mutate the element on its first yield, read it back on its second.
    for i, p in each(pts):
        if seen % 2 == 0:
            p.x += 100
        else:
            print("reference:", i, p.x)
        seen += 1


def value_element() -> None:
    nums = [10, 20]
    for i, n in each(nums):
        print("value:", i, n)


def reused_source() -> None:
    nums = [30, 40]
    # The source is REUSED after the consuming call, so sema cannot move it into
    # the `Own` slot and copies instead -- TPy's generator walks the copy where
    # CPython's walks `nums` itself. The mutation is kept after the loop so both
    # sides print the same lines; the warning is the acknowledgment that a
    # mutation DURING the loop would be seen by CPython and not by TPy.
    for i, n in each(nums):  # tpyc: warning(/copies/)
        print("reuse:", i, n)
    nums.append(50)
    print("reuse: len", len(nums))


def main() -> None:
    reference_element()
    value_element()
    reused_source()


main()

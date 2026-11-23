# A coroutine or generator call's argument reaches the one frame-argument
# row whatever its arm -- a scalar, an open `T`, a borrow tuple, a field
# read of a temporary -- and the temporaries it borrows live for the
# statement that consumes the call.
from typing import Iterator, Protocol
from tpy import int32
import part_arms_tuples
import part_arms_receivers
import part_arms_generic
import part_arms_heads
import part_arms_await
import part_arms_mixed
import part_keep_await
import part_keep_fields
import part_frame_order


class HasVal(Protocol):
    def val(self) -> int32: ...


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def val(self) -> int32:
        return self.n


def pick(s: Src) -> Src:
    return s


def gen(s: Src) -> Iterator[int32]:
    yield s.n
    yield s.n + 1


def gen_t[T: HasVal](s: T) -> Iterator[int32]:
    yield s.val()
    yield s.val() + 1


def gen_gen[T](x: T) -> Iterator[T]:
    yield x


def n5() -> int32:
    return 5


def consume(g: Iterator[int32]) -> int32:
    total = 0
    for v in g:
        total += v
    return total


def drain_src(g: Iterator[Src]) -> int32:
    total = 0
    for v in g:
        total += v.n
    return total


def scalar_and_generic() -> None:
    # the scalar twin, and an open `T` held by reference at a reference
    # type: the direct and the nested temporary live for the statement
    print("arms scalar", consume(gen(pick(Src(5)))))  # tpyc: ok
    print("arms generic", consume(gen_t(pick(Src(5)))))  # tpyc: ok
    print("arms generic direct", drain_src(gen_gen(Src(8))))  # tpyc: ok
    # a by-value nested argument stays where it is
    print("arms by value", consume(gen(Src(n5()))))  # tpyc: ok


class Holder:
    s: Src

    def __init__(self, n: int32) -> None:
        self.s = Src(n)


def main() -> None:
    scalar_and_generic()
    part_arms_tuples.main()
    part_arms_receivers.main()
    part_arms_generic.main()
    part_arms_heads.main()
    part_arms_await.main()
    part_arms_mixed.main()
    part_keep_await.main()
    part_keep_fields.main()
    part_frame_order.main()


main()
# a fresh field-read receiver at module level
print("arms module", consume(gen(Holder(4).s)))  # tpyc: ok

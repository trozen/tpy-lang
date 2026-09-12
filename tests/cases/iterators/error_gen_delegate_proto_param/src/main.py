# Delegating to a generator with a protocol-typed parameter inside a
# resumable frame is rejected cleanly: the callee struct carries deduced
# T_<pname> template args the __for_src field type cannot spell yet.
from typing import Iterable, Iterator
from tpy import int32


def doubled(items: Iterable[int32]) -> Iterator[int32]:
    yield 0
    for x in items:
        yield x * 2


def gen(xs: list[int32]) -> Iterator[int32]:
    yield -1
    for v in doubled(xs):  # tpyc: error(/protocol-typed parameters/)
        yield v


def main() -> None:
    for v in gen([1, 2]):
        print(v)


main()

# A None-narrowed pointer-repr `Optional[container]` NAME at a structural
# `Iterable`/`Sequence` slot: the argument must carry its `(*name)` deref. The
# arm that binds a container name bare at that slot asks containerhood first,
# like its sibling predicates: a name whose C++ form is not itself a range needs
# the indirection, and the bare read would drop it. No FIELD section: the same
# narrowed read off `self.items` rejects earlier, at `field.result_type`.
from typing import Iterable
from tpy import int32


def total(c: Iterable[int32]) -> int32:
    n = 0
    for v in c:
        n += v
    return n


def bump(c: list[int32]) -> None:
    c.append(99)


# free function: a narrowed Optional LOCAL at the Iterable slot.
def local_name() -> int32:
    maybe: list[int32] | None = [1, 2, 3]
    if maybe is None:
        return -1
    return total(maybe)  # tpyc: ok


# free function: the same shape from a PARAM.
def param_name(maybe: list[int32] | None) -> int32:
    if maybe is None:
        return -1
    return total(maybe)  # tpyc: ok


# free function: the plain container name, which stays bare.
def plain(xs: list[int32]) -> int32:
    return total(xs)  # tpyc: ok


# free function: the slot borrows, so a mutation through it is visible here --
# read-only output could not tell the borrow from a copy.
def mutate_through(maybe: list[int32] | None) -> int32:
    if maybe is None:
        return -1
    bump(maybe)
    return total(maybe)  # tpyc: ok


class Reader:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    # method: the same narrowed read inside a record method body.
    def sum_of(self, maybe: list[int32] | None) -> int32:
        if maybe is None:
            return -1
        return total(maybe)  # tpyc: ok


def main() -> None:
    print("local", local_name())
    print("param", param_name([4, 5]), param_name(None))
    print("plain", plain([8, 9]))
    print("mutate", mutate_through([1, 2]))
    print("method", Reader("r").sum_of([10, 20]))


main()

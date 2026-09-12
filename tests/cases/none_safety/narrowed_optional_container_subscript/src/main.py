# Regression: a narrowed pointer-repr `Optional[container]` local/param receiver
# used to render a RAW `(*lst)[i]` subscript READ, skipping index normalization
# and the KeyError path. Every subscript READ below is the subject.
from tpy import int32, readonly


def read_list(lst: list[int32] | None) -> None:
    if lst is None:
        return
    print(lst[-1])          # negative index must normalize (was: garbage)
    i = -2
    print(lst[i])           # same via a variable index


def aug_assign(lst: list[int32] | None) -> None:
    if lst is None:
        return
    lst[-1] += 5            # the READ half of the read-modify-write was raw
    print(lst[0], lst[1], lst[2])


def read_dict(d: dict[int32, int32] | None) -> None:
    if d is None:
        return
    try:
        print(d[99])        # missing key must raise, not default-insert
    except KeyError:
        print("KeyError")
    print(len(d))           # guards the silent insert: stays 2


def read_dict_readonly(d: readonly[dict[int32, int32]] | None) -> None:
    if d is None:
        return
    # A const receiver: the raw `operator[]` fallback was ill-formed C++.
    print(d[1])  # tpyc: ok


def read_bytearray(b: bytearray | None) -> None:
    if b is None:
        return
    print(b[-1])            # bytearray shares the pointer-repr receiver shape


def read_nested(rows: list[list[int32]] | None) -> None:
    if rows is None:
        return
    print(rows[-1][-1])     # the OUTER subscript is the narrowed one


class Doubler:
    n: int32

    def __init__(self) -> None:
        self.n = 3

    def __len__(self) -> int32:
        return self.n

    def __getitem__(self, i: int32) -> int32:
        return i * 2


def read_user_record(g: Doubler | None) -> None:
    if g is None:
        return
    for i in range(len(g)):
        # Bounds-proven index on a user record: the receiver type now resolves
        # to the record, so no gratuitous size_t cast (matches the non-Optional
        # sibling's render).
        print(g[i])


def main() -> None:
    read_list([1, 2, 3])
    aug_assign([1, 2, 3])
    read_dict({1: 10, 2: 20})
    read_dict_readonly({1: 10, 2: 20})
    read_bytearray(bytearray(b"abc"))
    read_nested([[1, 2], [3, 4]])
    read_user_record(Doubler())


main()

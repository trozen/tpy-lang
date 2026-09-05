# A value-position `and`/`or` over two same-typed reference-form operands is
# the truthy ternary ALIASING the chosen operand, never a copy. The admission
# is the reference axis, so `Array[T, N]` reaches this arm alongside the
# records and the other containers.
#
# The value-vs-reference distinction is forced two ways, because no one lever
# covers every leg. Mutating an OPERAND anywhere in the body retypes the whole
# select to `bool` in sema (BUGS.md#container-select-mutation-retypes-bool),
# but mutating through the RESULT does not -- so the set / bytearray / Array
# legs write through `v` and read the operand back in the caller. The list and
# dict legs carry @nocopy elements instead, so a copying render fails the C++
# build; that is the only lever they have, since a `set` element must be
# copy-constructible and a `bytearray` element is always UInt8.
from tpy import Int32, Array, nocopy


@nocopy
class Tag:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def pick_list(a: list[Tag], b: list[Tag]) -> Int32:
    v = a or b  # tpyc: ok
    return len(v)


def pick_dict(a: dict[str, Tag], b: dict[str, Tag]) -> Int32:
    v = a or b  # tpyc: ok
    return len(v)


def pick_set(a: set[Int32], b: set[Int32]) -> Int32:
    v = a or b  # tpyc: ok
    v.add(5)
    return len(v)


def pick_bytearray(a: bytearray, b: bytearray) -> Int32:
    v = a or b  # tpyc: ok
    v.append(90)
    return len(v)


def pick_array(a: Array[Int32, 2], b: Array[Int32, 2]) -> Int32:
    # An Array is never empty, so `or` always yields the left operand; the
    # write through the result is what proves which one it aliases.
    v = a or b  # tpyc: ok
    v[0] = 42
    return v[0]


def main() -> None:
    e1: list[Tag] = []
    l1: list[Tag] = [Tag(1)]
    print(pick_list(e1, l1))

    de: dict[str, Tag] = {}
    d1: dict[str, Tag] = {"a": Tag(1)}
    print(pick_dict(de, d1))

    se: set[Int32] = set()
    s1 = {1, 2}
    print(pick_set(se, s1))
    print(len(s1), len(se))          # the add landed on s1, not on se

    be = bytearray(b"")
    b1 = bytearray(b"xyz")
    print(pick_bytearray(be, b1))
    print(len(b1), len(be))

    a1 = Array[Int32, 2]([7, 8])
    a2 = Array[Int32, 2]([1, 2])
    print(pick_array(a1, a2))
    print(a1[0], a2[0])              # the write landed on a1, not on a2


main()

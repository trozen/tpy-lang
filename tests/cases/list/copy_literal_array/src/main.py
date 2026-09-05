# copy() of a literal-seeded local that resolves to `Array[T, N]` -- the
# sibling of copy_literal_list, whose three legs never resize and so never
# demote to `list`. The copy row admits on the reference axis, which `Array`
# is on.
# Each leg writes through the COPY and prints both, so a silent alias prints
# the written value twice.
from tpy import copy, Int32, Array


def literal_array() -> None:
    xs = [1, 2]           # unmutated size, so the literal resolves to Array
    ys = copy(xs)         # the flipped row: an Array-resolved copy source
    ys[0] = 9
    print(xs[0], ys[0])


def annotated_array() -> None:
    xs = Array[Int32, 2]([3, 4])  # the spelled-out inverse of the leg above
    ys = copy(xs)
    ys[1] = 9
    print(xs[1], ys[1])


def nested_array() -> None:
    # An Array of Arrays: the copy is deep, so writing into the copy's own
    # element leaves the source element alone.
    xs = [[1, 2], [3, 4]]
    ys = copy(xs)
    ys[0][0] = 9
    print(xs[0][0], ys[0][0])


def main() -> None:
    literal_array()
    annotated_array()
    nested_array()


main()

# Inverse of tuple_unpack_own_named_source: a value-type tuple unpacked from a
# named local must NOT trigger the owned-source move (no owned target) -- it
# still ref-binds the source (`const auto& __tup`). Guards the move branch
# against over-firing on value tuples.
from tpy import int32


def make() -> tuple[int32, int32]:
    return (1, 2)


def main() -> None:
    t = make()
    a, b = t
    print(a + b)


main()

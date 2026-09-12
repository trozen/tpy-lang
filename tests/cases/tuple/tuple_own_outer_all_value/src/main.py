# `Own[tuple[T, T]]` where every T is a value type: own_tuple_target() takes
# the all-value/Own short-circuit and returns the inner tuple unchanged
# (no per-element Own-wrapping needed).
from tpy import int32, Own


def make_pair() -> Own[tuple[int32, int32]]:
    return (int32(10), int32(20))


def main() -> None:
    pair = make_pair()
    print(pair[0])
    print(pair[1])


main()

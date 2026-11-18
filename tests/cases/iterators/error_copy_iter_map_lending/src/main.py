# `map`'s lending `Iterable[T]` input refuses a `copy_iter` of reference
# elements even where the callable takes ownership (`Own[list[int32]]`):
# map's signature does not yet keep its input's step ownership
# (BUGS.md#map-owned-iterable-own-callback-no-overload).
from tpy import int32, Own, copy_iter


def take(i: int32, xs: Own[list[int32]]) -> Own[list[int32]]:
    xs.append(i)
    return xs


def main() -> None:
    rows: list[list[int32]] = [[7]]
    # the refused call, with the remedy: collect the copies first
    for zs in map(take, [0], copy_iter(rows)):  # tpyc: error(/No matching overload for 'map'.*collect them first/)
        print(len(zs))


main()

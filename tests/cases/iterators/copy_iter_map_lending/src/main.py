# `map` over a `copy_iter` of reference elements with a callable that takes
# ownership (`Own[list[int32]]`): each copy is handed to the callable, so a
# write to the result never reaches `rows`, as in CPython.
from tpy import int32, Own, copy_iter


def take(i: int32, xs: Own[list[int32]]) -> Own[list[int32]]:
    xs.append(i)
    return xs


def main() -> None:
    rows: list[list[int32]] = [[7]]
    # the subject: map's input keeps the copy_iter's owned steps
    for zs in map(take, [0], copy_iter(rows)):  # tpyc: ok
        zs.append(9)
        print(len(zs), len(rows[0]))


main()

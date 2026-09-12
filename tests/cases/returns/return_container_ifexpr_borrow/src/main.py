# A same-type lvalue TERNARY at a container BORROW return slot: the C++
# ternary over two lvalues is itself an lvalue, so it binds the `T&` slot
# bare. The reference ladder's ifexpr arm, reached by containers since the
# record and container return arms merged.
from tpy import int32


def longer(a: list[int32], b: list[int32]) -> list[int32]:
    # Both arms are plain lvalue names, so the ternary returns bare.
    return a if len(a) >= len(b) else b  # tpyc: ok


def wider(a: dict[int32, int32], b: dict[int32, int32]) -> dict[int32, int32]:
    # The same arm over a dict.
    return a if len(a) >= len(b) else b  # tpyc: ok


def fuller(a: set[int32], b: set[int32]) -> set[int32]:
    # ... and over a set.
    return a if len(a) >= len(b) else b  # tpyc: ok


def main() -> None:
    la: list[int32] = [1, 2, 3]
    lb: list[int32] = [4]
    got = longer(la, lb)
    # The borrow return aliases the argument -- a copy would leave 3 here.
    got.append(99)
    print(len(la), la[3])
    da: dict[int32, int32] = {1: 1, 2: 2}
    db: dict[int32, int32] = {3: 3}
    d = wider(da, db)
    d[7] = 7
    print(len(da), da[7])
    sa: set[int32] = {1, 2}
    sb: set[int32] = {3}
    s = fuller(sa, sb)
    s.add(9)
    print(len(sa))


main()

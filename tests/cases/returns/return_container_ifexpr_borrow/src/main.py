# A same-type lvalue TERNARY at a container BORROW return slot: the C++
# ternary over two lvalues is itself an lvalue, so it binds the `T&` slot
# bare. The reference ladder's ifexpr arm, reached by containers since the
# record and container return arms merged.
from tpy import Int32


def longer(a: list[Int32], b: list[Int32]) -> list[Int32]:
    # Both arms are plain lvalue names, so the ternary returns bare.
    return a if len(a) >= len(b) else b  # tpyc: ok


def wider(a: dict[Int32, Int32], b: dict[Int32, Int32]) -> dict[Int32, Int32]:
    # The same arm over a dict.
    return a if len(a) >= len(b) else b  # tpyc: ok


def fuller(a: set[Int32], b: set[Int32]) -> set[Int32]:
    # ... and over a set.
    return a if len(a) >= len(b) else b  # tpyc: ok


def main() -> None:
    la: list[Int32] = [1, 2, 3]
    lb: list[Int32] = [4]
    got = longer(la, lb)
    # The borrow return aliases the argument -- a copy would leave 3 here.
    got.append(99)
    print(len(la), la[3])
    da: dict[Int32, Int32] = {1: 1, 2: 2}
    db: dict[Int32, Int32] = {3: 3}
    d = wider(da, db)
    d[7] = 7
    print(len(da), da[7])
    sa: set[Int32] = {1, 2}
    sb: set[Int32] = {3}
    s = fuller(sa, sb)
    s.add(9)
    print(len(sa))


main()

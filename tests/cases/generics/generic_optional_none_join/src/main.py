# A generic `T | None` return is force_pointer_repr (T* even for value
# T); joining it with None via make_union (ternary / if-else branch merge) must
# preserve that repr, not re-type the local as std::optional<T> (uncompilable).
# Uses Int32 so the value-typed force_pointer_repr path is exercised while the
# narrowed return stays a trivial copy -- a heap-backed T would also exercise a
# separate move-out-of-borrow issue, which Int32 avoids.
from tpy import Int32


def first[T](xs: list[T]) -> T | None:
    for x in xs:
        return x
    return None


def via_ternary(xs: list[Int32], c: bool) -> Int32:
    y = first(xs) if c else None  # tpyc: type(Int32 | None)
    if y is not None:
        return y
    return -1


def via_branches(xs: list[Int32], c: bool) -> Int32:
    if c:
        z = first(xs)
    else:
        z = None
    if z is not None:
        return z
    return -1


def main() -> None:
    xs = [Int32(10), Int32(20), Int32(30)]
    print(via_ternary(xs, True))
    print(via_ternary(xs, False))
    print(via_branches(xs, True))
    print(via_branches(xs, False))


main()

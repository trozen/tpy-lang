# Return list literals from functions with Own[list[T]] return type.
# Tests return-type-aware inference: [] and [x, y] resolve to list, not array.
from tpy import Int32, Own


def make_empty[T]() -> Own[list[T]]:
    return []


def make_single[T](x: T) -> Own[list[T]]:
    # The element is an open `T`, so the body states its copy contract here.
    return [x]  # tpyc: warning(/may copy T into owned storage/)


def make_list(x: Int32) -> Own[list[Int32]]:
    return [x, x + 1, x + 2]


def main():
    # Empty list from generic function
    a: list[Int32] = make_empty[Int32]()
    a.append(99)
    print(len(a))
    print(a[0])

    # Single-element list from generic function (T inferred from arg)
    b: list[Int32] = make_single(42)
    b.append(100)
    for x in b:
        print(x)

    # Multi-element list from non-generic function
    c: list[Int32] = make_list(10)
    c.append(100)
    for x in c:
        print(x)


main()

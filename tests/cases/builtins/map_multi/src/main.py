# Multi-iterable map: map(fn, a, b, ...) applies fn element-wise
from tpy import int32

def add(a: int32, b: int32) -> int32:
    return a + b

def add3(a: int32, b: int32, c: int32) -> int32:
    return a + b + c

def main() -> None:
    xs = [1, 2, 3]
    ys = [10, 20, 30]

    # Two-iterable map with function ref
    print(list(map(add, xs, ys)))

    # Two-iterable map with lambda
    print(list(map(lambda x, y: x * y, [2, 3, 4], [5, 6, 7])))

    # Different-length iterables (stops at shortest)
    print(list(map(add, [1, 2], [10, 20, 30])))

    # Three-iterable map
    print(list(map(add3, xs, ys, [100, 200, 300])))

    # Three-iterable map with lambda
    print(list(map(lambda a, b, c: a + b + c, [1, 2], [10, 20], [100, 200])))

    # Lazy iteration (two-iterable)
    for v in map(add, xs, ys):
        print(v)

main()

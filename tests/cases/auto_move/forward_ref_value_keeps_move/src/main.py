# Guard: a forward-referenced callee returning a value type (Int32) cannot
# borrow, so the later consume still auto-moves silently.
from tpy import Int32, Own


def main():
    xs = [1, 2, 3]
    n = count(xs)
    print(drop(xs))  # tpyc: ok
    print(n)


def count(xs: list[Int32]) -> Int32:
    return len(xs)


def drop(xs: Own[list[Int32]]) -> Int32:
    store: list[list[Int32]] = []
    store.append(xs)
    return len(store)


main()

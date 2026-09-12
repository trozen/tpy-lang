# Guard: a forward-referenced callee returning a value type (int32) cannot
# borrow, so the later consume still auto-moves silently.
from tpy import int32, Own


def main():
    xs = [1, 2, 3]
    n = count(xs)
    print(drop(xs))  # tpyc: ok
    print(n)


def count(xs: list[int32]) -> int32:
    return len(xs)


def drop(xs: Own[list[int32]]) -> int32:
    store: list[list[int32]] = []
    store.append(xs)
    return len(store)


main()

# Move-preservation guard: a value-typed call result (len) registers no
# borrow, so a later consume of the container still auto-moves silently
# even while the result is live.
from tpy import Int32, Own


def drop(xs: Own[list[Int32]]) -> Int32:
    store: list[list[Int32]] = []
    store.append(xs)
    return len(store)


def main():
    xs = [1, 2, 3]
    n = len(xs)
    print(drop(xs))  # tpyc: ok
    print(n)


main()

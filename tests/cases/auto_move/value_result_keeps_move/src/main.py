# Move-preservation guard: a value-typed call result (len) registers no
# borrow, so a later consume of the container still auto-moves silently
# even while the result is live.
from tpy import int32, Own


def drop(xs: Own[list[int32]]) -> int32:
    store: list[list[int32]] = []
    store.append(xs)
    return len(store)


def main():
    xs = [1, 2, 3]
    n = len(xs)
    print(drop(xs))  # tpyc: ok
    print(n)


main()

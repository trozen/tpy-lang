# zip() builtin over different iterables and arities

def main() -> None:
    # two lists of different types
    names = ["alice", "bob", "charlie"]
    ages = [30, 25, 35]
    for name, age in zip(names, ages):
        print(name, age)

    # different lengths (shortest wins)
    long = [1, 2, 3, 4, 5]
    short = ["x", "y"]
    for n, s in zip(long, short):
        print(n, s)

    # empty list
    empty: list[str] = []
    nums = [1, 2, 3]
    for s, n in zip(empty, nums):
        print(s, n)

    # three iterables
    xs = [1, 2, 3]
    ys = ["a", "b", "c"]
    zs = [True, False, True]
    for x, y, z in zip(xs, ys, zs):
        print(x, y, z)

    # four iterables
    ws = [0.5, 1.5]
    for x, y, z, w in zip(xs, ys, zs, ws):
        print(x, y, z, w)

    # five iterables
    vs = ["p", "q"]
    for x, y, z, w, v in zip(xs, ys, zs, ws, vs):
        print(x, y, z, w, v)

main()

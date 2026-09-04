# list/set built from an iterable via the ctor template (list(range(n)),
# list(xs), set(xs)); sources stay live afterwards (non-last-use args).


def main():
    xs = list(range(3))
    ys = list(xs)
    zs = set(xs)
    print(len(xs), len(ys), len(zs))
    print(xs[0], ys[1])


main()

# A tuple literal whose elements are list literals binds `tuple[list, list]`;
# the embedded pending element types must be finalized in the binding. Also
# covers a nested composite: a list of tuples of lists.
def main() -> None:
    t = ([1, 2], [3, 4])  # tpyc: ok
    print(t)
    t[0][0] = 9
    print(t)

    xs = [([1, 2], [3, 4])]  # tpyc: ok  (list[tuple[list, list]])
    print(xs)
    xs[0][1][0] = 8
    print(xs)

    # Alias the nested composite and mutate through the alias: the change must
    # be visible on the original (reference semantics, not a silent copy).
    ys = xs
    ys[0][0][0] = 7
    print(xs)

main()

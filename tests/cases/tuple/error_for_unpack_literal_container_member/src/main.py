# The adjacent shape to the literal-iterable unpack: a CONTAINER member in the
# element tuple binds a borrow target, which the unpack head does not lower.
def main() -> None:
    for xs, n in [([1, 2], 3), ([4], 5)]:  # tpyc: error(/tuple.ref_target/)
        print(len(xs), n)


main()

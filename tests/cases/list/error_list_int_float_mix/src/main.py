# An unannotated list literal refuses mixed int and float elements: CPython
# keeps each one's own type (docs/LANGUAGE_FEATURES.md, numeric tower).


def main() -> None:
    # A float element type would print the int as 1.0.
    xs = [1, 2.5]  # tpyc: error(/List literal has mixed types: element 2 is float, but earlier elements are int; CPython keeps each value's own type, so write 1\.0 instead of 1, or annotate the target as list\[float\]$/)
    print(xs)


main()

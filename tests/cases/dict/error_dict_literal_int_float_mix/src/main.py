# An unannotated dict literal refuses mixed int and float values: CPython
# keeps each one's own type (docs/LANGUAGE_FEATURES.md, numeric tower).


def main() -> None:
    # A float value type would print the int 1 as 1.0.
    d = {"a": 1, "b": 2.5}  # tpyc: error(/Dict has mixed value types: value 2 is float, but earlier values are int; CPython keeps each value's own type, so write 1\.0 instead of 1, or annotate the target as dict\[str, float\]$/)
    print(d)


main()

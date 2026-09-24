# An int literal does not join a float list: CPython keeps the int, so the
# arms share no type (docs/LANGUAGE_FEATURES.md, Conditionals).
def main(c: bool) -> None:
    e: list[float] = []
    # `[7]` would otherwise be pinned to `list[float]` and print `[7.0]`.
    y = e if c else [7]  # tpyc: error(/Incompatible types in ternary expression: 'list\[float\]' and 'list\[int32\]'/)
    print(y)


main(False)

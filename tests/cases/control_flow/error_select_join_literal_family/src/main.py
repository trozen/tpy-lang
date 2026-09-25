# An int literal does not join a float list: CPython keeps the int, so the
# arms share no type (docs/LANGUAGE_FEATURES.md, Conditionals).
def main(c: bool) -> None:
    e: list[float] = []
    # `[7]` would otherwise be pinned to `list[float]` and print `[7.0]`.
    y = e if c else [7]  # tpyc: error(/this conditional expression mixes int and float elements, and CPython keeps whichever value it picks; write 7\.0 instead of 7, or annotate the target as list\[float\]$/)
    print(y)


main(False)

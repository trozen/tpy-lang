# An unannotated list literal keeps the numeric family its values are written
# in: a list[float] parameter does not turn its ints into floats.
def scale(v: list[float]) -> None:
    v.append(2.5)


def main() -> None:
    ys = [1]
    # The list holds ints; CPython would still print them as ints.
    scale(ys)  # tpyc: error(/'ys' holds integer values, and it is passed here as list\[float\]; write its values as floats, or convert them: annotate its first binding: ys: list\[float\]/)
    print(ys)


main()

# sorted() takes key and reverse by keyword only, as in CPython
# (TypeError: sorted expected 1 argument, got 2).


def main() -> None:
    xs = [3, 1, 2]
    print(sorted(xs, True))  # tpyc: error(/'sorted' takes 1 positional argument\(s\), got 2/)


main()

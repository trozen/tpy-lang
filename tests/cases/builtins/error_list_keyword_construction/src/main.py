# list() takes its iterable positionally only, as in CPython
# (TypeError: list() takes no keyword arguments): the construction binds
# through the stub's `__init__`, which refuses the keyword.


def main() -> None:
    xs = [1, 2]
    print(list(iterable=xs))  # tpyc: error(/'list' parameter 'iterable' is positional-only/)


main()

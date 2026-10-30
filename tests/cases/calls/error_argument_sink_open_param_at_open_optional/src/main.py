# An open `T` parameter forwarded to an open `T | None` parameter: at a str /
# bytes instantiation the parameter is a view no `T | None` form takes, so it
# is refused for every T (an element or field of `T` passes; argument_sink).


def some[T](x: T | None) -> bool:
    return x is not None


def wrap[T](x: T) -> bool:
    return some(x)  # tpyc: error(/call\.generic_arg_shape/)


def main() -> None:
    print(wrap("s"))


main()

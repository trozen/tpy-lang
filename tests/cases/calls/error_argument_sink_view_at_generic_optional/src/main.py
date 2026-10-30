# A str parameter's view at a generic `T | None` parameter instantiated at
# str: that parameter owns its string, so the view would be copied unseen.


def some[T](x: T | None) -> bool:
    return x is not None


def direct(s: str) -> bool:
    return some(s)  # tpyc: error(/call\.generic_arg_shape/)


def main() -> None:
    print(direct("s"))


main()

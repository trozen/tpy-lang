# An integer type argument past 2**64 - 1 in a `*args` annotation is refused
# with the function's location (docs/LANGUAGE_FEATURES.md "Integer Type
# Parameters").


class U[N: int]:
    pass


def f(*xs: U[18446744073709551616]) -> None:  # tpyc: error(/integer type argument N = 18446744073709551616 of 'U' is out of range \(0 to 18446744073709551615\)/)
    print(len(xs))


def main() -> None:
    f()


main()

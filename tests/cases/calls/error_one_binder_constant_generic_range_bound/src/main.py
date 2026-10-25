# An integer type argument past 2**64 - 1 in a function type parameter's
# bound is refused at the function, though the bound never reaches C++
# (docs/LANGUAGE_FEATURES.md "Integer Type Parameters").
from tpy import int32


class U[N: int]:
    pass


def f[T: U[18446744073709551616]](x: T) -> int32:  # tpyc: error(/integer type argument N = 18446744073709551616 of 'U' is out of range \(0 to 18446744073709551615\)/)
    return 1


def main() -> None:
    print(0)


main()

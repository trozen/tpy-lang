# An integer type argument past 2**64 - 1 has no representation and is
# refused where it is written (docs/LANGUAGE_FEATURES.md "Integer Type
# Parameters": 0 to 2**64 - 1).


class U[N: int]:
    pass


def main() -> None:
    U[18446744073709551616]()  # tpyc: error(/integer type argument N = 18446744073709551616 of 'U' is out of range \(0 to 18446744073709551615\)/)


main()

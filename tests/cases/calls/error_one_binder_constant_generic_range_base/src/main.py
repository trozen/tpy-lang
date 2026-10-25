# An integer type argument past 2**64 - 1 in a base class is refused at the
# class, as in an annotation or a constructor call (docs/LANGUAGE_FEATURES.md
# "Integer Type Parameters": 0 to 2**64 - 1, refused where written).


class U[N: int]:
    pass


class S(U[18446744073709551616]):  # tpyc: error(/integer type argument N = 18446744073709551616 of 'U' is out of range \(0 to 18446744073709551615\)/)
    pass


def main() -> None:
    print(S())


main()

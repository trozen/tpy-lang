# An integer type argument past 2**64 - 1 of a builtin type in an annotation
# is refused there, as a class's is (docs/LANGUAGE_FEATURES.md "Integer Type
# Parameters").
from tpy import Array, int32


def main() -> None:
    a: Array[int32, 18446744073709551616]  # tpyc: error(/integer type argument N = 18446744073709551616 of 'Array' is out of range \(0 to 18446744073709551615\)/)
    print(len(a))


main()

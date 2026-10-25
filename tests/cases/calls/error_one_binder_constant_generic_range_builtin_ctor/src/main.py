# A builtin constructor refuses an integer type argument past 2**64 - 1
# nested in its element type, at the call (docs/LANGUAGE_FEATURES.md
# "Integer Type Parameters").
from tpy import Array, int32


def main() -> None:
    a = list[Array[int32, 18446744073709551617]]()  # tpyc: error(/integer type argument N = 18446744073709551617 of 'Array' is out of range \(0 to 18446744073709551615\)/)
    print(len(a))


main()

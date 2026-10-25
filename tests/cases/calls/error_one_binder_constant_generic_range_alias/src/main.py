# An integer type argument past 2**64 - 1 in a type alias body, here inside
# a Callable's result, is refused at the alias (docs/LANGUAGE_FEATURES.md
# "Integer Type Parameters").
from typing import Callable


class U[N: int]:
    pass


type F = Callable[[], U[18446744073709551616]]  # tpyc: error(/integer type argument N = 18446744073709551616 of 'U' is out of range \(0 to 18446744073709551615\)/)


def main() -> None:
    print(U[1]())


main()

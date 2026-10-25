# An integer type argument past 2**64 - 1 in a structural protocol's
# method signature is refused at the protocol, though the signature never
# reaches C++ (docs/LANGUAGE_FEATURES.md "Integer Type Parameters").
from typing import Iterator, Protocol


class U[N: int]:
    pass


class P(Protocol):  # tpyc: error(/integer type argument N = 18446744073709551616 of 'U' is out of range \(0 to 18446744073709551615\)/)
    def items(self) -> Iterator[U[18446744073709551616]]: ...


def main() -> None:
    print(0)


main()

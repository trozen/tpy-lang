# A default whose type does not match the declared parameter type is rejected
# at the `def`, rather than reaching codegen as `std::string_view s = 5`.
from tpy import Int32


def width(s: str = 5) -> Int32:  # tpyc: error(/expected str, got IntLiteral/)
    return len(s)


def main() -> None:
    print(width())


main()

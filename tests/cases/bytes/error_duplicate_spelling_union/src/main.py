# A pointer-variant union whose members render ONE C++ type (bytes and
# bytearray both spell std::vector<uint8_t>): std::holds_alternative<T> and
# std::get<T> are ill-formed on a repeated alternative, so the widened member
# class keeps a duplicate-spelling union out and the reject stays located.
from tpy import Int32


def bytes_or_bytearray(u: bytes | bytearray) -> Int32:
    if isinstance(u, bytearray):  # tpyc: error(/cond.call/)
        return len(u)
    return 0


def main() -> None:
    print(1)


main()

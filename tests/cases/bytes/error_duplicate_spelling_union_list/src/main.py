# The list leg of the duplicate-spelling union: list[UInt8] and bytes also
# share std::vector<uint8_t>. This shape reached ill-formed C++ (an unlocated
# g++ error on the repeated variant alternative) before the member class
# started checking that the alternatives are distinct.
from tpy import Int32, UInt8


def list_or_bytes(u: list[UInt8] | bytes) -> Int32:
    if isinstance(u, bytes):  # tpyc: error(/cond.call/)
        return len(u)
    return 1


def main() -> None:
    print(1)


main()

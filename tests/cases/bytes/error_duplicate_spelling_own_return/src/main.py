# The Own[union] return slot is the second consumer of the widened member
# class: an Own[bytes | bytearray] return renders
# std::variant<std::vector<uint8_t>, std::vector<uint8_t>>, whose converting
# ctor is ambiguous, so the duplicate-spelling union stays out of that slot too.
from tpy import Int32, Own


def own_union_return(n: Int32) -> Own[bytes | bytearray]:
    b = bytearray(n)
    return b  # tpyc: error(/return.slot_type/)


def main() -> None:
    print(1)


main()

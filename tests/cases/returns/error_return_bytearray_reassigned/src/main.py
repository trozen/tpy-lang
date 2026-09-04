# A bytearray NAME returns bare at the Own slot only while the binding is
# never reassigned -- the reassigned local keeps rejecting, like every other
# container family at that slot.
from tpy import Int32, Own


def make(n: Int32) -> Own[bytearray]:
    buf = bytearray(n)
    if n > 2:
        buf = bytearray(2)
    return buf  # tpyc: error(/return\.container_source/)


def main() -> None:
    b = make(4)
    for v in b:
        print(v)


main()

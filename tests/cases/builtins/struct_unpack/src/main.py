# struct.unpack_from, struct.unpack, struct.calcsize
from struct import unpack_from, unpack, calcsize
from tpy import Int32

def main() -> None:
    ba = bytearray(16)
    ba[0] = 1
    ba[2] = 2
    ba[4] = 255
    ba[5] = 255
    ba[6] = 3
    data: bytes = bytes(ba)

    # unpack_from with uint16
    a, b = unpack_from('<HH', data, 0)
    print(a)  # 1
    print(b)  # 2

    # unpack_from with int16 at offset
    c, d = unpack_from('<hh', data, Int32(4))
    print(c)  # -1
    print(d)  # 3

    # unpack (no offset)
    e, f = unpack('<HH', data)
    print(e)  # 1
    print(f)  # 2

    # calcsize
    print(calcsize('<HH'))     # 4
    print(calcsize('<II8s'))   # 16
    print(calcsize('<BBB'))    # 3
    print(calcsize('<hh2xI'))  # 10

    # bool format
    ba2 = bytearray(2)
    ba2[0] = 1
    ba2[1] = 0
    t, f2 = unpack_from('<??', bytes(ba2), 0)
    print(t)   # True
    print(f2)  # False

main()

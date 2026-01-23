from tpy import Int32

def test_aug_assign():
    x: Int32 = 10

    # Addition
    x += 5
    print(x)  # 15

    # Subtraction
    x -= 3
    print(x)  # 12

    # Multiplication
    x *= 2
    print(x)  # 24

    # Division (floor division for integer semantics)
    x //= 4
    print(x)  # 6

    # Modulo
    x %= 4
    print(x)  # 2

    # Bitwise AND
    x = 15
    x &= 9   # 0b1111 & 0b1001 = 0b1001
    print(x)  # 9

    # Bitwise OR
    x = 9
    x |= 6   # 0b1001 | 0b0110 = 0b1111
    print(x)  # 15

    # Bitwise XOR
    x = 15
    x ^= 6   # 0b1111 ^ 0b0110 = 0b1001
    print(x)  # 9

    # Left shift
    x = 1
    x <<= 4
    print(x)  # 16

    # Right shift
    x = 32
    x >>= 2
    print(x)  # 8

test_aug_assign()

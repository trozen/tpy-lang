# int.bit_length() returns the number of bits to represent abs(self).
# Matches CPython: 0 -> 0, sign is ignored.
def main() -> None:
    print(int(0).bit_length())
    print(int(1).bit_length())
    print(int(2).bit_length())
    print(int(3).bit_length())
    print(int(255).bit_length())
    print(int(256).bit_length())
    print(int(1023).bit_length())
    print(int(1024).bit_length())

    # Negative: same as abs.
    print(int(-1).bit_length())
    print(int(-128).bit_length())
    print(int(-2147483648).bit_length())  # 32 bits

    # Big values beyond 64 bits.
    print((int(1) << 64).bit_length())   # 65
    print((int(1) << 100).bit_length())  # 101
    print(((int(1) << 200) - int(1)).bit_length())  # 200 (all ones in 200 bits)

    # As an expression on a variable.
    n: int = int(0xDEADBEEF)
    print(n.bit_length())  # 32

main()

# An int needle in bytes membership must be a valid byte value: an
# out-of-range needle raises ValueError (matching CPython), including a
# BigInt needle beyond int32.
def main():
    b = b"ABC"
    k: int = 66
    print(k in b)
    print(200 in b)
    big: int = 1099511627776  # 2**40
    try:
        print(big in b)
    except ValueError:
        print("out of byte range")


main()

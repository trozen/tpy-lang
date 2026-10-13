# A str right-hand side is not an iterable of bytes: bytearray slice
# assignment keeps rejecting it.
def main() -> None:
    ba = bytearray(b"abcd")
    ba[0:2] = "ab"  # tpyc: error(/does not conform to protocol Iterable\[uint8\]/)
    print(ba)


main()

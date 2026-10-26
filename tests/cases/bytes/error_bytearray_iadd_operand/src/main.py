# `+=` on a bytearray with an operand no `__iadd__` variant takes names the
# variants and the operand in Python terms.
def main() -> None:
    ba = bytearray(b"a")
    ba += 1  # tpyc: error(/expected bytes or readonly\[bytearray\] or BytesView, got int/)
    print(ba)


main()

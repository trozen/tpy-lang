# Slice assignment into a container ELEMENT keeps rejecting: the element
# place gets no loan on its container (BUGS.md#element-place-slice-no-loan).
def g(bufs: list[bytearray]) -> None:
    bufs[0][0:2] = b"xy"  # tpyc: error(/not yet supported by C\+\+ code generation.*setitem\.slice/)


def main() -> None:
    print(1)


main()

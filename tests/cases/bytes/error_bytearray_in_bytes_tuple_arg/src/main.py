# The TUPLE-element face of the owning-bytes-sink rule. A tuple holds its
# elements by value, so each element is an owning slot even when the tuple
# itself is BORROWED -- a plain argument. Forwarding the tuple's own borrowing
# verdict into the element check let this shape through with no diagnostic at
# all, so it is pinned separately from the three other error cases: the tuple
# recursion is its own path, and compilation stops at the first error.
def f(t: tuple[bytes, int]) -> int:
    b, n = t
    return len(b)


def main() -> None:
    ba = bytearray(b"hello")
    print(f((ba, 1)))  # tpyc: error(/write 'bytes\(\.\.\.\)' around it/)


main()

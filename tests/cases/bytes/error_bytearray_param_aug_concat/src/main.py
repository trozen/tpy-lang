# `+=` on a bytearray PARAM: the concat-and-assign row takes locals only, so the
# param target rejects. The local-target sibling is pinned by
# tests/cases/bytes/bytearray.
def use(got: bytearray) -> None:
    got += b"xy"  # tpyc: error(/aug_assign/)
    print(bytes(got).decode())


def main() -> None:
    use(bytearray())


main()

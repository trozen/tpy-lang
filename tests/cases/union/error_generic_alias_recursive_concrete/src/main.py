# A constant type argument at the recursive position (Bad[int] instead of
# Bad[T]) is rejected -- v1 supports identity recursion only.

# tpyc: error(/recursive position of alias 'Bad' must reuse type parameter 'T' at position 0/)
type Bad[T] = T | list[Bad[int]]


def main() -> None:
    print("never reached")


main()

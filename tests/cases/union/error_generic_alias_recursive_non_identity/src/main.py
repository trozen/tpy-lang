# v1 supports identity recursion only: a recursive position that grows the
# type argument (list[T] instead of bare T) is rejected by the validator.

# tpyc: error(/recursive position of alias 'Weird' must reuse type parameter 'T' at position 0/)
type Weird[T] = T | list[Weird[list[T]]]


def main() -> None:
    print("never reached")


main()

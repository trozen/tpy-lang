# Arity mismatch at a generic alias use site is rejected at parse time
# with a clear "takes N type arguments, got M" diagnostic.
from tpy import Int32

type Pair[T] = tuple[T, T]


def main() -> None:
    # tpyc: error(/Type alias 'Pair' takes 1 type argument, got 2/)
    p: Pair[Int32, str] = (Int32(1), "x")
    print(p)


main()

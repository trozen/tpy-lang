# Bare reference to a generic alias (`x: Pair` without `[T]`) is
# rejected at parse-resolution rather than letting the body's
# TypeParamRefs leak into the annotation. Mirrors the "Generic protocol
# requires type arguments" pattern.
from tpy import Int32

type Pair[T] = tuple[T, T]


def main() -> None:
    x: Pair = (Int32(1), Int32(2))  # tpyc: error(/Generic type alias 'Pair' requires type arguments/)
    print(x)


main()

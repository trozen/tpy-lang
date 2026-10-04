# A tuple is no reference type even when it holds one: it is returned by
# value, so a `T: ReferenceType` result would be a copy of the tuple. The
# shape most likely to be taken for one, unlike a number or a str. Either
# wording: an inferred type argument that misses its bound reports the
# inference failure (TODO.md "Imprecise diagnostic for an inference-path
# bound violation").
from tpy import ReferenceType


class Box:
    def __init__(self, n: int) -> None:
        self.n = n


def touch[T: ReferenceType](x: T) -> T:
    return x


def main() -> None:
    t = (Box(1), 2)
    touch(t)  # tpyc: error(/does not satisfy bound 'ReferenceType'|Cannot infer type arguments for 'touch'/)


main()

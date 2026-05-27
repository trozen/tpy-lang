# isinstance() on a bare generic alias name is rejected. Generic aliases
# have no runtime identity; the resolver expanded the use sites to the
# body, so there's no Pair class to test against.
from tpy import Int32

type Pair[T] = tuple[T, T]


def main() -> None:
    p = (Int32(1), Int32(2))
    if isinstance(p, Pair):  # tpyc: error(/does not support generic type alias 'Pair'/)
        print(p)


main()

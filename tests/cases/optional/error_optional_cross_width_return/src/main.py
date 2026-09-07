# Returning an `Int8 | None` param at an `Int32 | None` slot: the whole optional
# has to be cast, where the narrowed read would deref the inner instead.
from tpy import Int8, Int32


def widen(p: Int8 | None) -> Int32 | None:
    if p is not None:
        return p  # tpyc: error(/stmt\.return/)
    return None


def main() -> None:
    print(widen(3), widen(None))


main()

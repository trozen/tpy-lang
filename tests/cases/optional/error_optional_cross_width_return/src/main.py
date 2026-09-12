# Returning an `int8 | None` param at an `int32 | None` slot: the whole optional
# has to be cast, where the narrowed read would deref the inner instead.
from tpy import int8, int32


def widen(p: int8 | None) -> int32 | None:
    if p is not None:
        return p  # tpyc: error(/stmt\.return/)
    return None


def main() -> None:
    print(widen(3), widen(None))


main()

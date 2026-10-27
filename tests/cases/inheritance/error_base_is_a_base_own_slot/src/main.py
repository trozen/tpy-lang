# An owned base slot refuses a dict subclass, as it refuses a record
# subclass: storing it as the base would drop the subclass part (the
# documented rule, docs/LANGUAGE_FEATURES.md "A base is a base": `Own[Base]`
# refuses a subclass).
from tpy import int32, Own


class Counts(dict[str, int32]):
    def __init__(self) -> None:
        pass


def keep(m: Own[dict[str, int32]]) -> int32:
    return len(m)


def main() -> None:
    c = Counts()
    print(keep(c))  # tpyc: error(/expected dict\[str, int32\], got Counts/)


main()

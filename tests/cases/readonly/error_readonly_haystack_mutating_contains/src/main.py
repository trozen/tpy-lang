# A readonly haystack still requires `__contains__` itself to be readonly: a
# mutating one is rejected like any mutating method call on a readonly receiver.
from tpy import readonly, int32


class Counted:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    @readonly(False)
    def __contains__(self, k: int32) -> bool:
        self.hits += 1
        return k > 0


def probe(c: readonly[Counted]) -> bool:
    return 3 in c  # tpyc: error(/non-readonly method '__contains__' on readonly reference/)


def main() -> None:
    print(probe(Counted()))


main()

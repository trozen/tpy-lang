# __move__ is inlined into a noexcept move ctor, so a raise reaching it would
# terminate. Sema rejects an un-try-guarded raise; a raise under a `try` (which
# may be caught locally) is left alone -- the guarded one below is NOT flagged.
from tpy import Int32, Own


class Holder:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def __del__(self) -> None:
        pass

    def __move__(self, other: Own[Holder]) -> None:
        try:
            raise ValueError("guarded")  # tpyc: ok
        except ValueError:
            pass
        if other.x < 0:
            raise ValueError("negative")  # tpyc: error(/must not raise/)
        self.x = other.x


def main() -> None:
    h = Holder(1)


main()

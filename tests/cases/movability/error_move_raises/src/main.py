# __move__ is inlined into a noexcept move ctor, so a raise reaching it would
# terminate. Sema rejects an un-try-guarded raise; the accepted counterpart, a
# raise a local handler catches, is pinned by
# tests/cases/movability/pool_custom_move.
from tpy import int32, Own


class Holder:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __del__(self) -> None:
        pass

    def __move__(self, other: Own[Holder]) -> None:
        if other.x < 0:
            raise ValueError("negative")  # tpyc: error(/must not raise/)
        self.x = other.x


def main() -> None:
    h = Holder(1)


main()

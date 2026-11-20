# `T: Sync` at `T = Own[L]` is asked of `L`, and the refusal explains why
# `L` is not Sync (its list field, pinned in diag.txt), not why the owned
# slot is not.
from tpy import Fn, Own, Sync, int32


class L:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1]


def make() -> Own[L]:
    return L()


def f_sync[T: Sync](mk: Fn[[], T]) -> int32:
    return 1


def main() -> None:
    print(f_sync[Own[L]](make))  # tpyc: error(/'Own\[L\]' does not satisfy bound 'Sync'/)


main()

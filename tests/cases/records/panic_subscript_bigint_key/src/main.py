# A BigInt key at a record __getitem__(k: int32) is the explicit call's checked
# narrowing: one `to_fixed_check` on the key, which panics when the value does
# not fit, as `t.__getitem__(k)` does.
from tpy import int32


class T:
    def __init__(self) -> None:
        pass

    def __getitem__(self, k: int32) -> int32:
        return k + 1


def main() -> None:
    t = T()
    small: int = 41
    print(t[small])
    big: int = 1099511627776
    # the key narrows once, at the variant's parameter
    print(t[big])


main()

# Subscripting a user record (custom __getitem__) narrowed out of a union keys
# the fi lookup on the narrowed member, so the record's __getitem__ fires; the
# narrowed name aliases the union member (mutating it is observed), matching
# CPython.
from tpy import Own


class Bag:
    xs: list[int]
    def __init__(self) -> None:
        self.xs = [10, 20]
    def __getitem__(self, i: int) -> int:
        return self.xs[i]


def get(flag: bool) -> Own[Bag | int]:
    if flag:
        return Bag()
    return 0


def main() -> None:
    v = get(True)
    if isinstance(v, Bag):
        print(v[1])   # tpyc: ok
        v.xs[1] = 99  # mutate through the narrowed reference
        print(v[1])   # 99 -- proves v aliases the union member, not a copy


main()

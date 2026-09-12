# A fixed-length comprehension of a __del__-bearing record (directly, or via a
# __del__-bearing field that deletes the wrapper's copy ops) builds a stack
# Array by aggregate construction: no default ctor, no assignment, move-out
# through the record's drop-flag move ops.
from tpy import int32


class Res:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __del__(self) -> None:
        pass


class Wrap:
    res: Res
    tag: int32

    def __init__(self, v: int32) -> None:
        self.res = Res(v)
        self.tag = v * 100


def main() -> None:
    xs = [Res(i) for i in range(4)]  # tpyc: type(/Array\[Res, 4\]/)
    total = 0
    for r in xs:
        total += r.v
    print(total)
    ws = [Wrap(i) for i in range(3)]  # tpyc: type(/Array\[Wrap, 3\]/)
    print(ws[0].tag, ws[2].res.v)


main()

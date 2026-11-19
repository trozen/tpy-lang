# The callees and records the sections share.
from tpy import int32, Own


def grow(ys: list[int32]) -> bool:
    ys.append(7)
    return True


def grow_len(ys: list[int32]) -> int32:
    ys.append(7)
    return len(ys)


def grow_rows(rs: list[list[int32]]) -> bool:
    rs.append([7])
    return True


def grow_set(s: set[int32]) -> bool:
    s.add(len(s) + 100)
    return True


def pick(ys: list[int32]) -> list[int32]:
    return ys


def grow_strs(ys: list[str]) -> bool:
    ys.append("z")
    return True


def pick_strs(ys: list[str]) -> list[str]:
    return ys


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def bump(self, by: int32) -> int32:
        self.v += by
        return self.v


class Box:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def items_m(self) -> list[int32]:
        return self.items


def mk_box() -> Own[Box]:
    return Box()

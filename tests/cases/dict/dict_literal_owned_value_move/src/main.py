# A last-use @nocopy local used as a dict LITERAL value moves into the map;
# @nocopy makes a silent copy a hard compile error, so a passing build proves it.
from tpy import int32
from tplib.box import Box


def value_move() -> None:
    p = Box(7)
    q = Box(8)
    d: dict[int32, Box[int32]] = {0: p, 1: q}
    print(d[0].get(), d[1].get())


def not_last_use() -> None:
    inner: list[int32] = [1, 2]
    # inner read after -> copied, not moved (asserts the over-trigger guard)
    d: dict[int32, list[int32]] = {0: inner}  # tpyc: warning(/copies .* into owned storage/)
    inner.append(3)
    print(len(inner), len(d))  # 3 1 -- inner intact (would be 0 if wrongly moved)


def main() -> None:
    value_move()
    not_last_use()


main()

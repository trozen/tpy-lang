# A @nocopy local at its genuine last use inside a `with` body is moved, not
# copied -- the with body now participates in last-use analysis.
from tplib.box import Box
from tpy import int32


class Guard:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, et, ev, tb) -> bool:
        return False


def main() -> None:
    boxes: list[Box[int32]] = []
    with Guard():
        a = Box(7)
        boxes.append(a)
    print(len(boxes))


main()

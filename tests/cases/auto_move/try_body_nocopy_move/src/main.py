# A @nocopy local at its genuine last use inside a `try` body is moved -- not
# read on any exception path, so the try body acts like an ordinary block.
from tplib.box import Box
from tpy import Int32


def main() -> None:
    boxes: list[Box[Int32]] = []
    try:
        a = Box(7)
        boxes.append(a)
    except Exception:
        print("e")
    print(len(boxes))


main()

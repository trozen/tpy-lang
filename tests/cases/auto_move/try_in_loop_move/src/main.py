# A `try` inside a loop exercises the liveness fixpoint's try handling: a
# @nocopy local created and consumed in the try body moves each iteration.
from tplib.box import Box
from tpy import int32


def main() -> None:
    boxes: list[Box[int32]] = []
    for _ in range(3):
        try:
            a = Box(7)
            boxes.append(a)
        except Exception:
            print("e")
    print(len(boxes))


main()

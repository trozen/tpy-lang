# A finally body runs on mid-try exception paths, so facts the try body
# kills (even if later re-established) must not be assumed inside it.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


def boom() -> None:
    raise ValueError("boom")


def main():
    p: Point | None = Point(3)
    if p is None:
        return
    try:
        p = None
        boom()
        p = Point(4)
    finally:
        print(p.x)  # tpyc: warning(/Potential None access/)


main()

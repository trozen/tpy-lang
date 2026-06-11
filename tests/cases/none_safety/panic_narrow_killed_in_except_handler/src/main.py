# An exception can be thrown at ANY point in the try body, so a handler
# must not assume facts the body may have killed (runtime check).
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


def main():
    p: Point | None = Point(9)
    if p is None:
        return
    try:
        p = None
        raise ValueError("boom")
    except ValueError:
        print(p.x)  # tpyc: warning(/Potential None access/)


main()

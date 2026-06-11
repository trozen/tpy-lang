# A call may invoke a nonlocal-writing closure, so name-level narrowing
# dies at every call site once such a closure exists (runtime check).
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32):
        self.x = x


def main():
    p: Point | None = Point(7)

    def clear():
        nonlocal p
        p = None

    if p is not None:
        clear()
        print(p.x)  # tpyc: warning(/Potential None access/)


main()

# Narrowing proven before a loop must not survive the back-edge when the
# body kills it: iteration 2+ re-enters with p possibly None (runtime check).
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


def main():
    p: Point | None = Point(1)
    if p is None:
        return
    i = 0
    while i < 3:
        print(p.x)  # tpyc: warning(/Potential None access/)
        if i == 1:
            p = None
        i += 1


main()

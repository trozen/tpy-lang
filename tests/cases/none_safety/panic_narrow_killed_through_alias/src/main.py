# Mutating through a static alias kills field facts rooted at every
# member of the alias group: h2.clear() invalidates h.opt's narrowing.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32):
        self.x = x


class Holder:
    opt: Point | None

    def __init__(self, p: Point):
        self.opt = p

    def clear(self):
        self.opt = None


def main():
    h = Holder(Point(5))
    h2 = h
    if h.opt is not None:
        h2.clear()
        print(h.opt.x)  # tpyc: warning(/Potential None access/)


main()

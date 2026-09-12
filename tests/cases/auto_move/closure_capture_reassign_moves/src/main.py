# Move-preservation guard: a capture reassigned between the consume and
# the closure call reads the rebound object, so consuming the old object
# still auto-moves silently (the capture seed is killed by the rebind).
from tpy import int32, Own


class Point:
    items: list[int32]

    def __init__(self):
        self.items = [1, 2, 3]


class Sink:
    stored: list[Point]

    def __init__(self):
        self.stored = []

    def consume(self, p: Own[Point]):
        self.stored.append(p)


def main():
    s = Sink()
    p = Point()

    def show():
        print(len(p.items))

    s.consume(p)  # tpyc: ok -- p is rebound below before show() runs
    p = Point()
    p.items.append(4)
    show()


main()

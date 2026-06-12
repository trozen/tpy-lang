# A nested def captures p by reference: a consume AFTER the def must not
# auto-move p (the closure still reads it). Copy semantics at the consume
# are intended -- the copy warning is the designed diagnostic.
from tpy import Int32, Own


class Point:
    items: list[Int32]

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

    s.consume(p)  # tpyc: warning(/copies/)
    show()


main()

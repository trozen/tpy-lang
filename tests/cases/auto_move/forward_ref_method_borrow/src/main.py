# Method forward reference: run() is analyzed before first(), so the bind
# registers a conservative borrow and the consume copies (with warning).
from tpy import Int32, Own


class P:
    vals: list[Int32]

    def __init__(self):
        self.vals = [5]


class Picker:
    def run(self) -> Int32:
        xs = [P()]
        n = self.first(xs)
        r = drop(xs)  # tpyc: warning(/copies/)
        return r + n.vals[0]

    def first(self, xs: list[P]) -> P:
        return xs[0]


def drop(xs: Own[list[P]]) -> Int32:
    store: list[list[P]] = []
    store.append(xs)
    return len(store)


def main():
    p = Picker()
    print(p.run())


main()

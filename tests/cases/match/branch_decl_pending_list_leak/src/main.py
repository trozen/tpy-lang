# A list literal first declared inside a match arm and read after the
# match resolves in the arm's hoisted pre-declaration.
class Point:
    x: int
    y: int

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


def f(p: Point) -> int:
    match p:
        case Point(x=0):
            xs = [1, 2]
        case _:
            return -1
    xs.append(7)
    return xs[0] + len(xs)


def g(p: Point) -> int:
    match p:
        case Point(x=0):
            xs = [1, 2]
        case _:
            xs = [3]
    return xs[0] + len(xs)


def main() -> None:
    print(f(Point(0, 5)))
    print(f(Point(4, 5)))
    print(g(Point(0, 5)))
    print(g(Point(4, 5)))


main()

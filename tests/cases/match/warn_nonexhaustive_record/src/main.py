# warning: non-exhaustive match on record (guarded and literal-field arms only)
class Point:
    x: int
    y: int
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

def describe_guarded(p: Point) -> str:
    match p:  # tpyc: warning(/non-exhaustive match.*case _:/)
        case Point(x=x) if x > 0:
            return "positive x"
    return "other"

def describe_literal(p: Point) -> str:
    match p:  # tpyc: warning(/non-exhaustive match.*case _:/)
        case Point(x=0):
            return "origin-x"
    return "other"

def describe_exhaustive(p: Point) -> str:
    match p:  # tpyc: ok
        case Point(x=x, y=y):
            return "point"

def main() -> None:
    print(describe_guarded(Point(1, 2)))
    print(describe_guarded(Point(-1, 2)))
    print(describe_literal(Point(0, 5)))
    print(describe_literal(Point(1, 5)))
    print(describe_exhaustive(Point(3, 4)))

main()

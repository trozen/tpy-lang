# D16 v1.5 phase 7: hasattr on a class without __getattr__: declared members
# fold to True, anything else folds to False at compile time (no runtime call).

class Point:
    x: int
    y: int

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


def main() -> None:
    p = Point(1, 2)
    print(hasattr(p, "x"))     # declared -> True
    print(hasattr(p, "y"))     # declared -> True
    print(hasattr(p, "z"))     # absent + no dunder -> False (compile-time)


main()

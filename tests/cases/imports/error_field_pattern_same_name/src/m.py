# A second `Outer.Inner`, unrelated to main's.
class Outer:
    class Inner:
        v: int

        def __init__(self) -> None:
            self.v = 2

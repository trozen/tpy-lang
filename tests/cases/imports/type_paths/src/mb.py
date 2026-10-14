# A nested class deriving from m's nested class of the same dotted name: a
# base, not a cycle.
import m


class Outer:
    class Inner(m.Outer.Inner):  # tpyc: ok
        def __init__(self, v: int) -> None:
            super().__init__(v + 100)

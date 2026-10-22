# Refuses valid Python by the rule "Numeric widening across reassignments",
# Annotated (docs/LANGUAGE_FEATURES.md): an annotation goes on a variable's
# first binding, at module level as in a function, so one on a name the
# module already bound is refused.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


x = None
x: Point = Point(1)  # tpyc: error(/'x' is already bound at line 15; an annotation goes on the first binding/)

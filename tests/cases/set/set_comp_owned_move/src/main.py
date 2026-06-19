# Regression: a set comprehension filter condition builds a per-iteration
# Own[T] move-temp (Box(i) moves the last-use loop var); the temp must land
# inside the loop body, not the enclosing statement scope where the loop var is
# undeclared. (Set elements must be copyable, so the owned temp lives in the
# condition, not the element.)
from tpy import Int32
from tplib.box import Box


def is_small(b: Box[Int32]) -> bool:
    return b < Box(3)


def main(n: Int32) -> None:
    s = {i for i in range(n) if is_small(Box(i))}
    print(len(s))


main(6)

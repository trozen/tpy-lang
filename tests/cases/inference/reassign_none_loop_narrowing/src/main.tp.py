# None-seeded variable reassigned inside a loop should be Optional[T]
# after the loop, allowing narrowing with `is not None`.
from tpy import Int32

class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


x = None
for i in range(0, 2):
    x = Point(i)

if x is not None:
    print(x.x)

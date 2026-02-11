from tpy import Int32


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


x = None
for i in range(0, 2):
    x = Point(i)

if x is not None:
    print(x.x)  # tpyc: error(/Cannot access field 'x' on type None/)

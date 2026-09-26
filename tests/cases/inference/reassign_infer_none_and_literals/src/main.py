from tpy import int32, Own


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def make_point() -> Own[Point]:
    return Point(7)


def get_flag() -> bool:
    return True


p = None
p = make_point()
print(p.x)

n = None
n = 123
print(n)

z = 0
z = int32(666)
print(z)

f = 0.0
f = 1.5
print(f)

flag = None
flag = get_flag()
print(flag)

# Test imported record type used in function parameter and return type
from tpy import int32
from shapes import Circle


def get_radius(c: Circle) -> int32:
    return c.radius


def main() -> None:
    c = Circle(int32(10))
    print(get_radius(c))

main()

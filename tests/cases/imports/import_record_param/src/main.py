# Test imported record type used in function parameter and return type
from tpy import Int32
from shapes import Circle


def get_radius(c: Circle) -> Int32:
    return c.radius


def main() -> None:
    c = Circle(Int32(10))
    print(get_radius(c))

main()

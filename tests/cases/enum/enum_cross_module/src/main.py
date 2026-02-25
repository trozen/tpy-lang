# Test importing enum from another module
from colors import Color, color_value


def main() -> None:
    c = Color.Red
    print(c)
    print(c.name)
    print(color_value(c))

    g = Color.Green
    print(c == g)
    print(c != g)
    print(c == Color.Red)

main()

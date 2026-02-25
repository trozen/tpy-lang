# Cross-module enum with explicit type annotations in params, returns, and locals
from colors import Color

def describe(c: Color) -> str:
    if c == Color.Red:
        return "red"
    return "other"

def default_color() -> Color:
    return Color.Blue

def main() -> None:
    c: Color = Color.Green
    print(c)
    print(describe(Color.Red))
    print(default_color())

main()

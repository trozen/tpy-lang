# Cross-module import of a @native enum.
from lib_enum import Color


def main() -> None:
    print(Color.RED)
    print(Color.GREEN)
    print(Color.BLUE)
    print(Color.RED.value)


main()

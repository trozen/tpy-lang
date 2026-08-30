# Parenthesized or-pattern groups on an enum subject (the enum switch
# strategy): the group flattens into one set of switch labels.
from enum import Enum, auto


class Color(Enum):
    Red = auto()
    Green = auto()
    Blue = auto()
    Cyan = auto()


def classify(c: Color) -> str:
    match c:
        # Same labels as the flat `case Color.Red | Color.Green | Color.Blue:`.
        case (Color.Red | Color.Green) | Color.Blue:
            return "primary"
        case Color.Cyan:
            return "mixed"


def main() -> None:
    print(classify(Color.Red))
    print(classify(Color.Green))
    print(classify(Color.Blue))
    print(classify(Color.Cyan))


main()

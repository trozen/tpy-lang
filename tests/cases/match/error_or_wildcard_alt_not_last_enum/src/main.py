# Same arm-order rule over an enum subject: the wildcard alternative makes the
# group irrefutable, the group becomes the switch default, and the arms after it
# would otherwise stay live case labels. One rule across subject kinds, so this
# rejects exactly like the union form.
from enum import Enum


class Color(Enum):
    RED = 1
    GREEN = 2
    BLUE = 3


def pick(c: Color) -> str:
    match c:
        case Color.RED | _:
            return "rest"
        case Color.GREEN:  # tpyc: error(/unreachable case after wildcard pattern/)
            return "green"
        case Color.BLUE:
            return "blue"
    return "no"


def main() -> None:
    print(pick(Color.GREEN))


main()

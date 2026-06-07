# An enum-member field default naming a member the enum does not declare
# is rejected at registration.
from enum import Enum


class Color(Enum):
    RED = 0
    GREEN = 1


class Shape:
    tint: Color = Color.PURPLE  # tpyc: error(/not a member of enum 'Color'/)


def main() -> None:
    pass


main()

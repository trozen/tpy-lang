# Writing a getter-only enum property is rejected as for a record.
from enum import Enum


class Color(Enum):
    Red = 0

    @property
    def warm(self) -> bool:
        return True


def main() -> None:
    c = Color.Red
    c.warm = False  # tpyc: error(/Property 'warm' is read-only/)


main()

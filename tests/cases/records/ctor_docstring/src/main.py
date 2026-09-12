# A constructor docstring is non-init trivia: it emits no code and no source
# line, and the member-init chain is unaffected.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        """The x coordinate."""
        self.x = x


def main() -> None:
    print(Point(4).x)


main()

# An unbound call of the enum's own method through the enum is rejected, as
# for a record, and the message names the enum.
from enum import Enum


class Color(Enum):
    Red = 0

    def label(self) -> str:
        return "x"

    def other(self) -> str:
        return Color.label(self)  # tpyc: error(/'Color.label\(self, ...\)' calls a method of 'Color' itself/)


def main() -> None:
    print(Color.Red.other())


main()

# @native / @cpp_template are rejected on an enum method: its body is always
# emitted.
from enum import Enum
from tpy.extern import native


class Color(Enum):
    Red = 0
    Blue = 1

    @native("ns::label")
    def label(self) -> str:  # tpyc: error(/@native .* are not supported on enum methods/)
        ...


def main() -> None:
    print(Color.Red)


main()

# An async enum method is rejected: the resumable frame would borrow the
# temporary wrapper the member is called through.
from enum import Enum


class Color(Enum):
    Red = 0
    Blue = 1

    async def fetch(self) -> str:  # tpyc: error(/async methods on enums are not supported yet/)
        return "x"


def main() -> None:
    print(Color.Red)


main()

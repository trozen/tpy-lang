# A generator enum method is rejected: the resumable frame would borrow the
# temporary wrapper the member is called through.
from enum import Enum
from tpy import int32


class Color(Enum):
    Red = 0
    Blue = 1

    def counts(self) -> "Iterator[int32]":  # tpyc: error(/generator methods on enums are not supported yet/)
        yield self.value


def main() -> None:
    print(Color.Red)


main()

# Returning a locally-constructed generic @dynamic protocol value is
# rejected for the same reason as the non-generic case (the stack
# adapter would be destroyed). Verifies the rejection diagnostic
# renders the parameterized protocol name correctly.
from typing import Protocol
from tpy import Int32, dynamic


@dynamic
class Container[T](Protocol):
    def get(self) -> T:
        ...


class Box:
    def get(self) -> Int32:
        return 7


def make_container() -> Container[Int32]:
    return Box()  # tpyc: error(/Cannot return local or temporary as 'Container\[Int32\]'/)

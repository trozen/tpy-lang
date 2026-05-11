# Optional[generic @dynamic protocol] as a record field is rejected;
# diagnostic should render the parameterized name `Container[Int32]`.
from typing import Protocol, Optional
from tpy import Int32, dynamic


@dynamic
class Container[T](Protocol):
    def get(self) -> T:
        ...


class Holder:
    x: Optional[Container[Int32]]  # tpyc: error(/Optional\[Container\[Int32\]\] is not supported/)

    def __init__(self) -> None:
        self.x = None

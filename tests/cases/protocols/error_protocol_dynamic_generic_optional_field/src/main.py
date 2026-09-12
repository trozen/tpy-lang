# Optional[generic @dynamic protocol] as a record field is rejected;
# diagnostic should render the parameterized name `Container[int32]`.
from typing import Protocol, Optional
from tpy import int32, dynamic


@dynamic
class Container[T](Protocol):
    def get(self) -> T:
        ...


class Holder:
    x: Optional[Container[int32]]  # tpyc: error(/Optional\[Container\[int32\]\] is only supported at a parameter position/)

    def __init__(self) -> None:
        self.x = None

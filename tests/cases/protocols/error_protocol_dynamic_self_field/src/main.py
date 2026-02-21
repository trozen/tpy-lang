# @dynamic protocol with Self-typed field -- object-unsafe
from tpy import dynamic
from typing import Protocol, Self

@dynamic
class Container(Protocol):  # tpyc: error(/@dynamic protocol 'Container' cannot use Self type in field 'value'/)
    value: Self
    def get(self) -> str: ...

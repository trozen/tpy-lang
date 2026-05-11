# Self in a generic @dynamic protocol: still rejected.
# Object-safety rule from the original @dynamic design is independent
# of the protocol's genericity.
from typing import Protocol, Self
from tpy import dynamic


@dynamic
class Doubler[T](Protocol):  # tpyc: error(/@dynamic protocol 'Doubler' cannot use Self type/)
    def doubled(self) -> Self:
        ...

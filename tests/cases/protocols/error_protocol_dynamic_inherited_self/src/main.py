# @dynamic protocol inheriting Self-returning method from parent -- object-unsafe
from tpy import dynamic
from typing import Protocol, Self

class Clonable(Protocol):
    def clone(self) -> Self: ...

@dynamic
class DynClonable(Clonable, Protocol):  # tpyc: error(/@dynamic protocol 'DynClonable' cannot use Self type in method 'clone'/)
    def name(self) -> str: ...

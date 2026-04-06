# TypedDict: additional base classes are not allowed
from typing import TypedDict

class Mixin:
    pass

class Info(TypedDict, Mixin):  # tpyc: error(/cannot have additional base classes/)
    name: str

# BaseN.field only works when BaseN is a strict ancestor of the enclosing
# record -- unrelated classes are rejected, paralleling the v2.1
# BaseN.method(self, ...) ancestry rule.
from tpy import int32


class Ancestor:
    value: int32


class Unrelated:
    value: int32


class Child(Ancestor):
    def probe(self) -> int32:
        return Unrelated.value  # tpyc: error(/not an ancestor of 'Child'/)

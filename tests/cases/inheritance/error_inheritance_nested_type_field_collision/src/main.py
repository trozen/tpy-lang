# A class declares both a nested type and a same-name field; the generated
# C++ can't resolve `Container::Kind` once the field shadows the nested
# type, so sema rejects the collision outright and asks for a rename.
from enum import Enum
from tpy import int32


class Container:  # tpyc: error(/declares both a nested type 'Kind' and a field\/method 'Kind'/)
    class Kind(Enum):
        A = 1
        B = 2

    Kind: int32

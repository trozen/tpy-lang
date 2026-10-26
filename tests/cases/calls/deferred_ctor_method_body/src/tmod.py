# Imports a generic class under the name of its own type parameter.
from tpy import int32
from bags import Bag as T


class TUser:
    def count(self) -> int32:
        # a construction through the alias named like the type parameter
        b = T()  # tpyc: ok
        b.put(1)
        return len(b.items)

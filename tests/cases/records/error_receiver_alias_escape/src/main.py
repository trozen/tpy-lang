# Receiver-alias escapes obey docs/LANGUAGE_FEATURES.md's borrow lifetime rules.
from typing import Self
from tpy import int32, Own


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def escape(self: Own[Self]) -> Self:
        me = self
        # A borrow of the method's owned receiver cannot escape its return.
        return me  # tpyc: error(/Cannot return local or temporary/)

# Receiver-alias mutation obeys docs/LANGUAGE_FEATURES.md's readonly rules.
from tpy import Int32, readonly


class Cell:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    @readonly
    def mutate(self) -> None:
        me = self
        # The receiver cannot become mutable through a local alias.
        me.n += 1  # tpyc: error(/Cannot mutate readonly reference/)

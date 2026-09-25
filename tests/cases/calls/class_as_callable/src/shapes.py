# A class the main module imports and passes by name as a factory.
from tpy import int32


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

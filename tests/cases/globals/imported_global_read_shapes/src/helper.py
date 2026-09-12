"""Value globals of every family the importing module reads bare."""
from typing import Final
from tpy import int32, StrView

G: int32 = 5
label: StrView = "hello"
BIG: Final[int32] = 99
items: list[int32] = [1, 2]
maybe: int32 | None = 4

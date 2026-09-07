"""Value globals of every family the importing module reads bare."""
from typing import Final
from tpy import Int32, StrView

G: Int32 = 5
label: StrView = "hello"
BIG: Final[Int32] = 99
items: list[Int32] = [1, 2]
maybe: Int32 | None = 4

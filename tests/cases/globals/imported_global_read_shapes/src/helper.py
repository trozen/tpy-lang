"""Value globals of every family the importing module reads bare."""
from typing import Final
from tpy import Own, int32, StrView

G: int32 = 5
label: StrView = "hello"
BIG: Final[int32] = 99
items: list[int32] = [1, 2]
maybe: int32 | None = 4


def mk_labels() -> Own[list[str]]:
    return ["a" * 30, "b" * 30]


# A str global a loop over a temporary rebinds owns the last element.
rebound = "m"
for rebound in mk_labels():
    pass
churn = ["q" * 40, "r" * 40]

# A local one binding of which is outside a Literal parameter's set is
# refused there, naming the value (BUGS.md#literal-param-no-checked-str-spelling).
from typing import Literal


def take(m: Literal["r", "w"]) -> str:
    return m


def main() -> None:
    s = "r"
    if len(take("w")) > 0:
        s = "zz"
    print(take(s))  # tpyc: error(/expected Literal\["r", "w"\], got 's', which may hold "zz"; pass one of "r", "w"/)


main()

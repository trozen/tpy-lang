from typing import Literal
from tpy import int32
def go(mode: Literal["r", "w"]) -> int32:
    n = 0
    while mode == "r":
        n += 1
        if n > 2:
            break
    return n
def main() -> None:
    print(go("r"))
main()

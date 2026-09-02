from typing import Literal
from tpy import Int32
def go(mode: Literal["r", "w"]) -> Int32:
    n = 0
    while mode == "r":
        n += 1
        if n > 2:
            break
    return n
def main() -> None:
    print(go("r"))
main()

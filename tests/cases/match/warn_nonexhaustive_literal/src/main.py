# Non-exhaustive match on Literal type -- missing values produce warning
from typing import Literal

def incomplete(mode: Literal["r", "w", "rb"]) -> None:
    match mode:  # tpyc: warning(/missing: "rb"/)
        case "r":
            print("read")
        case "w":
            print("write")

def main() -> None:
    incomplete("r")
    incomplete("w")

main()

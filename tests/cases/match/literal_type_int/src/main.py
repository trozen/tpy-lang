# Match on Literal int type -- exhaustive, switch codegen
from typing import Literal

def priority_label(level: Literal[1, 2, 3]) -> str:
    match level:
        case 1:
            return "low"
        case 2:
            return "medium"
        case 3:
            return "high"

def main() -> None:
    print(priority_label(1))
    print(priority_label(2))
    print(priority_label(3))

main()

# error: duplicate case None in Optional match
from typing import Optional
from tpy import Int32

def check(v: Optional[Int32]) -> str:
    match v:
        case None:
            return "none"
        case None:  # tpyc: error(/duplicate case for None/)
            return "also none"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()

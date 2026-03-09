# warning: non-exhaustive match on Optional (missing None)
from typing import Optional
from tpy import Int32

def classify(x: Optional[Int32]) -> str:
    match x:  # tpyc: warning(/non-exhaustive match.*missing: None.*case _:/)
        case 0:
            return "zero"
        case 1:
            return "one"
    return "unknown"

def main() -> None:
    print(classify(Int32(0)))
    print(classify(Int32(1)))
    print(classify(Int32(5)))

main()

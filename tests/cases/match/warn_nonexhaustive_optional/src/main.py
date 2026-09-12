# warning: non-exhaustive match on Optional (missing None)
from typing import Optional
from tpy import int32

def classify(x: Optional[int32]) -> str:
    match x:  # tpyc: warning(/non-exhaustive match.*missing: None.*case _:/)
        case 0:
            return "zero"
        case 1:
            return "one"
    return "unknown"

def main() -> None:
    print(classify(int32(0)))
    print(classify(int32(1)))
    print(classify(int32(5)))

main()

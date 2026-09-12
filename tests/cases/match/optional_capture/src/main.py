# capture on Optional subject gets inner type, not Optional
from typing import Optional
from tpy import int32

def classify(x: Optional[int32]) -> str:
    match x:
        case None:
            return "none"
        case v:
            return str(v)

def describe(x: Optional[str]) -> str:
    match x:
        case None:
            return "empty"
        case s:
            return "got: " + s

def main() -> None:
    print(classify(None))
    print(classify(int32(42)))
    print(describe(None))
    print(describe("hello"))

main()

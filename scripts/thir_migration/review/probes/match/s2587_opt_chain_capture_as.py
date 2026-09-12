from typing import Optional
from tpy import int32

def f(o: Optional[int32]) -> str:
    match o:
        case 1:
            return "one"
        case x as y:
            if y is None:
                return "n"
            return str(y)

def main() -> None:
    print(f(2))

main()

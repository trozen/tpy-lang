from typing import Optional
from tpy import Int32

def f(o: Optional[Int32]) -> str:
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

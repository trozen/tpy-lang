from typing import Optional
from tpy import Int32

def f(o: Optional[Int32]) -> str:
    match o:
        case 1 | 2 as x:
            return str(x)
        case None:
            return "n"
        case _:
            return "o"

def main() -> None:
    print(f(1))

main()

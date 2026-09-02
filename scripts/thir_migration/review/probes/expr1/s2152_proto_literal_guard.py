import math
from tpy import Int32
def f(n: Int32) -> float:
    match n:
        case 1 if math.fsum([1.0, 2.0]) > 0.0:
            return 1.0
        case _:
            return 0.0
def main() -> None:
    print(f(1))
main()

import math
from tpy import int32
def f(n: int32) -> int32:
    match n:
        case 1 if math.prod(range(1, 4)) > 0:
            return 1
        case _:
            return 0
def main() -> None:
    print(f(1))
main()

from tpy import int32, Own
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

from tplib import Box
def describe(a: A) -> int32:
    return a.n

b = Box(A(3))

def main() -> None:
    k = 1
    match k:
        case 1 if describe(b) > 0:
            print("a")
        case _:
            print("b")
main()

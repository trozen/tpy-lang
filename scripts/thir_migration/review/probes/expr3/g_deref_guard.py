from tpy import Int32, Own
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

from tplib import Box
def describe(a: A) -> Int32:
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

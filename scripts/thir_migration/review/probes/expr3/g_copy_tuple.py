from tpy import int32, Own
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

from tpy import copy
def main() -> None:
    t = (A(1), 2)
    u = copy(t)
    print(u[1])
main()

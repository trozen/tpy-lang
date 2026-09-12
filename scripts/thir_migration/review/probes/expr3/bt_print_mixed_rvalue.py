from tpy import int32, Own
class A:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n

def mk() -> Own[A]:
    return A(2)
def main() -> None:
    a = A(1)
    print((a, mk()))
main()

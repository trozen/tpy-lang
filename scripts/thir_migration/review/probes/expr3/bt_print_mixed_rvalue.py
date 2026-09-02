from tpy import Int32, Own
class A:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n

def mk() -> Own[A]:
    return A(2)
def main() -> None:
    a = A(1)
    print((a, mk()))
main()

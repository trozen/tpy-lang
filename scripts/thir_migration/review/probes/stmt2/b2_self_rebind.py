from tpy import Int32
class A:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
    def go(self) -> Int32:
        self = A(2)
        return self.x
def main() -> None:
    print(A(1).go())
main()

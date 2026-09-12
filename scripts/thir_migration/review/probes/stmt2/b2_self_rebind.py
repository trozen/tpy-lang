from tpy import int32
class A:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
    def go(self) -> int32:
        self = A(2)
        return self.x
def main() -> None:
    print(A(1).go())
main()

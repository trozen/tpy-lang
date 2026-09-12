from tpy import int32
class Box:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
V = Box(2)
S: Box = V
V = Box(5)
def main() -> None:
    print(V.n, S.n)
main()

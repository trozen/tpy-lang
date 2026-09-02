from tpy import Int32
class Box:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
V = Box(2)
S: Box = V
V = Box(5)
def main() -> None:
    print(V.n, S.n)
main()

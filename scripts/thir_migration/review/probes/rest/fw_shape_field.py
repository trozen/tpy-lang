from tpy import Int32
class H:
    xs: list[Int32]
    def __init__(self) -> None:
        self.xs = [1]
    def take(self, o: H) -> None:
        self.xs = o.xs
def main() -> None:
    h = H()
    g = H()
    h.take(g)
    print(len(h.xs))
main()

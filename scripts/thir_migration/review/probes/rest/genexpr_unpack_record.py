from tpy import Int32
class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def main() -> None:
    items = [(P(1), 2), (P(3), 4)]
    print(sum(p.x for p, n in items))
main()

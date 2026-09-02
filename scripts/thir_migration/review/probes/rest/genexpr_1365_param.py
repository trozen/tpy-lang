from tpy import Int32
class P:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def f(p: P) -> None:
    for q in (p for i in range(2)):
        q.x = q.x + 1
def main() -> None:
    p = P(1)
    f(p)
    print(p.x)
main()

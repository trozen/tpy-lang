from tpy import Int32
class Stack[T]:
    items: list[T]
    def __init__(self) -> None:
        self.items = []
    def size(self) -> Int32:
        return len(self.items)
def f(s: Stack[Int32]) -> Int32:
    if (q := s).size() > 0:
        return q.size()
    return 0
def main() -> None:
    pass
main()

from tpy import Char, Own, Int32
class Sink:
    n: Int32
    def __init__(self) -> None:
        self.n = 0
    def put(self, c: Own[Char]) -> None:
        self.n += 1
def pick(s: Sink, flag: bool, a: Char, b: Char) -> None:
    s.put(a if flag else b)
def main() -> None:
    pick(Sink(), True, Char('a'), Char('b'))
main()

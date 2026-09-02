from tpy import Int32, Own
class Dog:
    n: Int32
    def __init__(self, n: Int32) -> None:
        self.n = n
type DS = Dog | str
def a(s: str) -> Own[DS]:
    return s
def main() -> None:
    print(1)
main()

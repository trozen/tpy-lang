from tpy import int32, Own
class Dog:
    n: int32
    def __init__(self, n: int32) -> None:
        self.n = n
type DS = Dog | str
def a(s: str) -> Own[DS]:
    return s
def main() -> None:
    print(1)
main()

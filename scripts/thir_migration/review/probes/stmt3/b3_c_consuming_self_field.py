from tpy import Int32, Own, nocopy
from typing import Self
@nocopy
class Holder:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def take(self: Own[Self]) -> str:
        return self.name
def main() -> None:
    h = Holder('x')
    print(h.take())
main()

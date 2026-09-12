from typing import TypedDict
from tpy import int32
class Info(TypedDict, total=False):
    name: str
    age: int32
class Holder:
    info: Info
    def __init__(self, i: Info) -> None:
        self.info = i
    def show(self) -> None:
        print(self.info.get("age", 0))
def main() -> None:
    h = Holder(Info(name="a"))
    h.show()
    hs = [Info(name="b")]
    print(hs[0].get("age", 1))
main()

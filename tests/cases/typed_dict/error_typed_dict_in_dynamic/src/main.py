# TypedDict: "key" in td requires string literal
from typing import TypedDict

class Info(TypedDict, total=False):
    name: str

def main() -> None:
    td = Info()
    key = "name"
    print(key in td)  # tpyc: error(/string literal/)

main()

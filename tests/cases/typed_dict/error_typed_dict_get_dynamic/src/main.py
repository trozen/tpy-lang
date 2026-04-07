# TypedDict: td.get() requires string literal key
from typing import TypedDict

class Info(TypedDict, total=False):
    name: str

def main() -> None:
    td = Info()
    key = "name"
    td.get(key)  # tpyc: error(/string literal/)

main()

# TypedDict: td.get() with wrong number of arguments
from typing import TypedDict

class Info(TypedDict, total=False):
    name: str

def main() -> None:
    td = Info()
    td.get("name", "a", "b")  # tpyc: error(/1 or 2/)

main()

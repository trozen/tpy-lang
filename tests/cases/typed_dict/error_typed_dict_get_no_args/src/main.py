# TypedDict: td.get() with no arguments
from typing import TypedDict

class Info(TypedDict, total=False):
    name: str

def main() -> None:
    td = Info()
    td.get()  # tpyc: error(/1 or 2/)

main()

# TypedDict: td.get() with unknown field
from typing import TypedDict

class Info(TypedDict, total=False):
    name: str

def main() -> None:
    td = Info()
    td.get("missing")  # tpyc: error(/has no key/)

main()

# TypedDict: "key" in td with unknown field
from typing import TypedDict

class Info(TypedDict, total=False):
    name: str

def main() -> None:
    td = Info()
    print("missing" in td)  # tpyc: error(/has no key/)

main()

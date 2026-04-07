# TypedDict: td.get() default type mismatch
from typing import TypedDict
from tpy import Int32

class Info(TypedDict, total=False):
    name: str

def main() -> None:
    td = Info()
    td.get("name", Int32(1))  # tpyc: error(/default value/)

main()

# TypedDict: td.get() default type mismatch
from typing import TypedDict
from tpy import int32

class Info(TypedDict, total=False):
    name: str

def main() -> None:
    td = Info()
    td.get("name", int32(1))  # tpyc: error(/default value/)

main()

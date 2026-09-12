# A subscript WRITE into a `total=False` TypedDict: Python allows writing an
# absent key, a shape the field-lvalue write target does not model.
from typing import TypedDict
from tpy import int32


class Info(TypedDict, total=False):
    age: int32


def main() -> None:
    d = Info(age=1)
    d["age"] = 2  # tpyc: error(/setitem.receiver/)
    print(d["age"])


main()

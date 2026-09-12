# TypedDict total=False: missing-field access throws KeyError -- catchable
# in user code (same throw site as dict[k] miss).
from typing import TypedDict
from tpy import int32


class Info(TypedDict, total=False):
    name: str
    age: int32


def main() -> None:
    partial = Info(name="Alice")
    try:
        print(partial["age"])
    except KeyError as e:
        print("caught:", str(e))


main()

# error: primitive type pattern that is not a union member
from tpy import Int32


class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name


def check(x: Int32 | Cat) -> str:
    match x:
        case str():  # tpyc: error(/not a member/)
            return "string"
        case _:
            return "other"
    return ""


def main() -> None:
    pass

main()

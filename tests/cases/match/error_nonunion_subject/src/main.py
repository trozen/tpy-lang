# error: match subject must be a union type
from tpy import Int32

def describe(x: Int32) -> str:
    match x:  # tpyc: error(/must be a union type/)
        case _:
            return "something"
    return ""

def main() -> None:
    pass

main()

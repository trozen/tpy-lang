# error: literal pattern type does not match subject type
from tpy import int32

def classify(n: int32) -> str:
    match n:
        case "hello":  # tpyc: error(/str literal pattern not valid/)
            return "string"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()

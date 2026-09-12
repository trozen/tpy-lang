# error: duplicate literal pattern on primitive subject
from tpy import int32

def classify(n: int32) -> str:
    match n:
        case 0:
            return "zero"
        case 0:  # tpyc: error(/duplicate case/)
            return "also zero"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()

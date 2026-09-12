# match/case on int subject with literal patterns
from tpy import int32

def classify(n: int32) -> str:
    match n:
        case 0:
            return "zero"
        case 1:
            return "one"
        case x:
            return "other"

def main() -> None:
    print(classify(0))
    print(classify(1))
    print(classify(42))

main()

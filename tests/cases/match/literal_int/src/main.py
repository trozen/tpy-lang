# match/case on int subject with literal patterns
from tpy import Int32

def classify(n: Int32) -> str:
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

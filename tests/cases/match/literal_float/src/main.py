# match/case on float subject with literal patterns
def classify(x: float) -> str:
    match x:
        case 0.0:
            return "zero"
        case 1.0:
            return "one"
        case other:
            return "other"

def main() -> None:
    print(classify(0.0))
    print(classify(1.0))
    print(classify(3.14))

main()

# match/case on local str variables (PendingStrType before resolution)
def classify_items(items: list[str]) -> None:
    for item in items:
        match item:
            case "apple":
                print("fruit")
            case "carrot":
                print("vegetable")
            case _:
                print("unknown: " + item)

def classify_many(items: list[str]) -> None:
    """Switch-optimized path (>= 5 literal cases)."""
    for item in items:
        match item:
            case "red":
                print("color")
            case "green":
                print("color")
            case "blue":
                print("color")
            case "cat":
                print("animal")
            case "dog":
                print("animal")
            case other:
                print("other: " + other)

def main() -> None:
    classify_items(["apple", "carrot", "banana"])
    classify_many(["red", "cat", "dog", "xyz"])

main()

# For-loop str variable stays std::string when mutated via augassign
def main() -> None:
    words: list[str] = ["hello", "world"]

    # Augmented assignment forces string ownership
    for w in words:
        w += "!"
        print(w)

main()

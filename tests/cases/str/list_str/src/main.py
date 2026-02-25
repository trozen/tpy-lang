# list[str] operations with owned strings
def main() -> None:
    words: list[str] = ["hello", "world", "foo"]
    print(len(words))
    print(words[0])
    print(words[1])
    words.append("bar")
    print(len(words))
    print(words[3])

    # Iterate over list of strings
    for w in words:
        print(w)

main()

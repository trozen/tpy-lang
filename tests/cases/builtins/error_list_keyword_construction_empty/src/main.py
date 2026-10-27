# An empty-container keyword value at list() is refused for its binding (the
# iterable is positional-only), not blamed for an uninferable element type.


def main() -> None:
    print(list(iterable=[]))  # tpyc: error(/No matching overload for 'list' with argument types \(iterable=/)


main()

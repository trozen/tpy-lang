# filter(None, iterable) filters falsy elements via truthiness
def main() -> None:
    # Filter falsy ints (0 is falsy)
    print(list(filter(None, [0, 1, 2, 0, 3])))

    # Filter falsy strings (empty string is falsy)
    print(list(filter(None, ["", "hello", "", "world"])))

    # Filter falsy bools
    print(list(filter(None, [True, False, True, False])))

    # All truthy
    print(list(filter(None, [1, 2, 3])))

    # All falsy
    print(list(filter(None, [0, 0, 0])))

    # Lazy iteration
    for x in filter(None, [0, 1, 0, 2]):
        print(x)

main()

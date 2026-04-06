# Stepped string slicing: s[start:stop:step] returns a new str.
def main() -> None:
    s: str = "hello world"

    # Every other char
    print(s[::2])

    # Reverse
    print(s[::-1])

    # Reverse with bounds
    print(s[4:0:-1])

    # Step with start
    print(s[1::3])

    # Edge: very negative start with negative step -> empty
    print(repr(s[-100::-1]))

main()

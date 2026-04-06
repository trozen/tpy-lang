# bytes-in-bytes substring containment
def main() -> None:
    data = b"hello world"
    print(b"world" in data)   # True
    print(b"xyz" in data)     # False
    print(b"" in data)        # True (empty always matches)
    print(b"hello world" in data)  # True (exact match)
    # Single byte via int still works
    print(104 in data)  # True ('h' = 104)
    print(0 in data)    # False
main()

# Stepped bytes slicing: b[start:stop:step] returns new bytes.
def main() -> None:
    data: bytes = b"abcdef"

    # Every other byte
    print(data[::2])

    # Reverse
    print(data[::-1])

    # Step with bounds
    print(data[1:5:2])

main()

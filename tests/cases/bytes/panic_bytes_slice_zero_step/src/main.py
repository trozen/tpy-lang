# Panic: slice step cannot be zero.
def main() -> None:
    data: bytes = b"hello"
    print(data[::0])

main()

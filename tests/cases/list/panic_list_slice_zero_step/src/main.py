# Panic: slice step cannot be zero.
def main() -> None:
    items = [1, 2, 3]
    print(items[::0])

main()

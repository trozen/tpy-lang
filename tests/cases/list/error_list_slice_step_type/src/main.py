# Error: slice step must be an integer type.
def main() -> None:
    items = [1, 2, 3, 4, 5]
    print(items[::0.5])  # tpyc: error(/Slice step must be an integer type/)

main()

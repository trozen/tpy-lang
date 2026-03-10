# Error: slice bounds must be integer types.

def main() -> None:
    items: list[int] = [10, 20, 30]
    print(items[0.5:2])  # tpyc: error(/integer type/)

main()

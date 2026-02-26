# Error: slicing a non-string type (list slicing not yet supported).

def main() -> None:
    items: list[int] = [1, 2, 3, 4, 5]
    print(items[1:3])  # tpyc: error(/not yet supported/)

main()

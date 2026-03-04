# hash() on unhashable type should error
def main() -> None:
    items: list[int] = [1, 2, 3]
    h = hash(items)  # tpyc: error(/No matching overload/)

main()

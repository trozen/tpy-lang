# Test str.join() -- join strings from a list
def main() -> None:
    # Basic join
    result = ",".join(["a", "b", "c"])
    print(result)

    # Empty separator
    together = "".join(["a", "b", "c"])
    print(together)

    # Single element
    single = ",".join(["only"])
    print(single)

    # Empty list
    empty_items: list[str] = []
    empty = ",".join(empty_items)
    print("empty:", len(empty))

    # Join with variable separator
    sep = " - "
    items = ["one", "two", "three"]
    print(sep.join(items))

main()

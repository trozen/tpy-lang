# list() from iterators, both standalone and nested inside generic calls

def main() -> None:
    # Standalone
    print(list(range(5)))
    print(list(x * 2 for x in [1, 2, 3]))
    words = ["hello", "world"]
    print(list(iter(words)))

    # Nested in generic calls with protocol params
    nums = [5, 3, 1, 4, 2]
    print(sorted(list(x * 2 for x in nums)))
    print(sum(list(range(5))))
    bools = [True, True, False]
    print(all(list(iter(bools))))

main()

# zip() with range and other non-list iterables

def main() -> None:
    # zip with range
    names = ["alice", "bob", "charlie"]
    for i, name in zip(range(3), names):
        print(i, name)

    # two ranges
    for a, b in zip(range(4), range(10, 14)):
        print(a, b)

main()

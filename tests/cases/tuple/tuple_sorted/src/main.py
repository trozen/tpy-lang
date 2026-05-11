# sorted() and list.sort() on list[tuple[...]] using lexicographic tuple
# ordering. Exercises the same Comparable conformance path as
# tuple_priority_queue but through the broader sorted() / .sort() surface.
def main() -> None:
    pairs = [
        (3, "c"),
        (1, "a"),
        (2, "b"),
        (1, "b"),
    ]

    for p, s in sorted(pairs):
        print(p, s)

    pairs.sort()
    print("---")
    for p, s in pairs:
        print(p, s)

main()

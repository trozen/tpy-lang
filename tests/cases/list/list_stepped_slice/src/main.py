# Stepped list slicing: a[start:stop:step] returns a new list.
def main() -> None:
    items = [1, 2, 3, 4, 5]

    # Every other element
    print(items[::2])

    # Reverse
    print(items[::-1])

    # Reverse with bounds
    print(items[3:0:-1])

    # Step with start
    print(items[1::2])

    # Step with start and stop
    print(items[0:4:2])

    # Negative step with bounds
    print(items[4:1:-1])

    # Step of 1 (same as basic slice but returns owned list)
    print(items[::1])

    # Empty result (step in wrong direction)
    print(items[0:4:-1])

    # Edge: very negative start with negative step -> empty
    print(items[-100::-1])

    # Edge: large positive start clamped
    print(items[100::-1])

main()

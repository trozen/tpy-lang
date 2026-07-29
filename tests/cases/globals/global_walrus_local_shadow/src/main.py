# Inverse of global_walrus_write: WITHOUT a `global` declaration a walrus binds
# a function-local that shadows the module variable, leaving it untouched --
# exactly what CPython does.

counter = 5


def observe() -> int:
    return counter


def shadow() -> int:
    v = (counter := 99)
    # Reads inside this function see the local shadow.
    return v + counter


def nested_shadow() -> int:
    # A nested def has its own scope: no `global` anywhere, so the inner walrus
    # binds an inner local. (A nested-def walrus whose name COLLIDES with a
    # module global is a separate open defect -- see BUGS.md.)
    def inner() -> int:
        return (tally := 3)

    return inner()


def main() -> None:
    print("shadow:", shadow())
    print("nested:", nested_shadow())
    print("module untouched:", observe(), counter)


main()

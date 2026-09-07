# A REASSIGNED bytearray alias cannot bind a reference; it would take the
# pointer-rebind form, which the value-slot alias arm does not carry.
def pick(a: bytearray, b: bytearray, go: bool) -> None:
    x = a  # tpyc: error(/decl.slot_type/)
    if go:
        x = b
    print(len(x))


def main() -> None:
    pick(bytearray(b"a"), bytearray(b"bc"), True)


main()

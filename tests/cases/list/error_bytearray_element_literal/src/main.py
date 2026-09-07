# A container literal whose element family is outside every routed family --
# `bytearray` elements land on the catch-all family reject.
def main() -> None:
    xs = [bytearray(b"a"), bytearray(b"b")]  # tpyc: error(/container_literal/)
    print(len(xs))


main()

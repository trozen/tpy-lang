# Error: except clause with a non-Name root (call result, subscript) is rejected.
# Dotted forms are accepted only when the root is a plain identifier.
def make() -> None:
    pass


def main() -> None:
    try:
        pass
    except make().Error:  # tpyc: error(/requires a simple or dotted name/)
        pass


main()

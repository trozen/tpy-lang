# A package __init__ in an import cycle with its submodule, in an order
# CPython imports: absolute and relative from-imports, a submodule read
# through its package or taken before it is bound there -- no warning.
import pkg
import rpk
import spk


def main() -> None:
    pkg.main()
    rpk.main()
    spk.main()


main()

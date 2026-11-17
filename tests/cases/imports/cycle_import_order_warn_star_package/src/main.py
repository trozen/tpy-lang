# A star import of a package whose `__all__` lists a submodule still being
# imported: CPython's star import fails on it, TPy's skips submodules. TPy
# warns at the star import and runs the program.
import sp.sub
import tp


def main() -> None:
    print("started submodule:", sp.sub.E().v)
    print("unattached submodule:", tp.sub.E().v)


main()

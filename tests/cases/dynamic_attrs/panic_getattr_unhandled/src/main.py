# Direct `obj.foo` access with no enclosing try/except: the throw propagates
# out of main() and the terminate handler prints the uncaught AttributeError.


class Bag:
    def __getattr__(self, name: str) -> str:
        raise AttributeError(name)


def main() -> None:
    b = Bag()
    print(b.missing)


main()

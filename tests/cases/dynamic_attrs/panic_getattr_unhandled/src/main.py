# Direct `obj.foo` access at top-level (no @error_return, no try/except):
# the dyn-attr miss panics at runtime since there's nowhere to propagate.


class Bag:
    def __getattr__(self, name: str) -> str:
        raise AttributeError(name)


def main() -> None:
    b = Bag()
    print(b.missing)


main()

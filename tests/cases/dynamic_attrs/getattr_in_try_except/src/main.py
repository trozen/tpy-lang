# Direct `obj.foo` access inside try/except AttributeError: the missing-attribute
# signal routes through the goto-based dispatch to the except clause.


class Bag:
    def __getattr__(self, name: str) -> str:
        if name == "host":
            return "example.com"
        raise AttributeError(name)


def main() -> None:
    b = Bag()
    try:
        v = b.host
        print(v)
        v = b.missing
        print("never:", v)
    except AttributeError as e:
        print("caught:", str(e))


main()

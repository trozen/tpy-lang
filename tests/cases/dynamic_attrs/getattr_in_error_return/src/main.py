# Direct `obj.foo` access inside an @error_return(AttributeError) caller:
# the AttributeError auto-propagates when the dunder reports missing.
from tpy import error_return


class Bag:
    def __getattr__(self, name: str) -> str:
        if name == "host":
            return "example.com"
        raise AttributeError(name)


@error_return(AttributeError)
def fetch(b: Bag, name: str) -> str:
    if name == "host":
        return b.host       # auto-propagate path: dunder returns OK
    return b.missing        # auto-propagate path: dunder raises -> caller returns unexpected


def main() -> None:
    b = Bag()
    try:
        print(fetch(b, "host"))
        print(fetch(b, "missing"))
    except AttributeError as e:
        print("caught:", str(e))


main()

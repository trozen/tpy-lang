# 3-arg getattr with Optional return type and None default: exercises the
# default coercion path (None -> Optional[str]).
from typing import Optional


class Bag:
    def __getattr__(self, name: str) -> Optional[str]:
        if name == "host":
            return "example.com"
        raise AttributeError(name)


def main() -> None:
    b = Bag()
    found = getattr(b, "host", None)
    if found is not None:
        print("host:", found)
    missing = getattr(b, "missing", None)
    if missing is None:
        print("missing is None")
    else:
        print("never")


main()

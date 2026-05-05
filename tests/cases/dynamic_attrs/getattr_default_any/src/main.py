# D16 v1.5 phase 8: 3-arg getattr on an Any-returning dyn-readable class.
# The default is coerced into Any; result type stays Any.
from typing import Any, cast


class Bag:
    def __getattr__(self, name: str) -> Any:
        if name == "count":
            return 7
        raise AttributeError(name)


def main() -> None:
    b = Bag()
    a: Any = getattr(b, "count", -1)
    print(cast(int, a))     # dunder -> 7
    a = getattr(b, "missing", -1)
    print(cast(int, a))     # dunder raises -> -1


main()

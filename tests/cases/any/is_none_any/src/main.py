# `x is None` and `x is not None` on Any check the typeid against
# the TPy NoneType (std::monostate in TPy's runtime).

from typing import Any


def main() -> None:
    a: Any = None
    b: Any = 42
    print(a is None)
    print(b is None)
    print(a is not None)
    print(b is not None)


main()

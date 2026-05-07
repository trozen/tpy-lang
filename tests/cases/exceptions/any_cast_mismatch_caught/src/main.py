# typing.cast(T, x) raises TypeError at runtime when the contained typeid
# does not match T. TPy-specific safety: CPython's cast is identity at
# runtime so the type-mismatch error doesn't exist there.

from typing import Any, cast


def main() -> None:
    x: Any = "not an int"
    try:
        n = cast(int, x)
        print(n)
    except TypeError as e:
        print("caught TypeError")
        # Don't print the exact message: it embeds C++-mangled-then-demangled
        # type names which depend on libstdc++/libc++ choice.


main()

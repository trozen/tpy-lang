# hash() on Any dispatches through the hash slot. Concrete hash values
# are platform/seed-dependent, so the test only verifies that hashing
# different keyed contents produces something usable in dict/set.

from typing import Any


def main() -> None:
    a: Any = 42
    b: Any = "hello"
    seen: set[int] = set()
    seen.add(hash(a))
    seen.add(hash(b))
    print(len(seen) >= 1)


main()

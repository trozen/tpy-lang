# __hash__ returning int (BigInt) -- covers positive, negative, and large values
from __future__ import annotations

class Key:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v

    def __hash__(self) -> int:
        return self.val

    def __eq__(self, other: Key) -> bool:
        return self.val == other.val

def main() -> None:
    # Positive (small path)
    a = Key(42)
    b = Key(42)
    print(hash(a) == hash(b))

    # As dict key
    d: dict[Key, str] = {}
    d[a] = "hello"
    print(d[b])

    # Negative (sign branch)
    c = Key(-1)
    e = Key(-1)
    print(hash(c) == hash(e))
    print(hash(c) == hash(a))

    # Large value (heap path, multi-limb)
    big = Key(10 ** 30)
    big2 = Key(10 ** 30)
    print(hash(big) == hash(big2))

main()

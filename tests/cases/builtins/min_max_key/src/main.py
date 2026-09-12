# min() and max() with key= parameter
from tpy import int32

def negate(x: int32) -> int32:
    return -x

def main() -> None:
    a: int32 = 3
    b: int32 = -5

    # min/max by absolute value
    print(min(a, b, key=lambda x: x if x >= 0 else -x))
    print(max(a, b, key=lambda x: x if x >= 0 else -x))

    # min/max with negate key (reverses ordering)
    print(min(a, b, key=negate))
    print(max(a, b, key=negate))

    # 3-arg min/max with key
    c: int32 = -1
    print(min(a, b, c, key=lambda x: x if x >= 0 else -x))
    print(max(a, b, c, key=lambda x: x if x >= 0 else -x))

    # strings by length
    s1 = "hello"
    s2 = "hi"
    print(min(s1, s2, key=lambda s: len(s)))
    print(max(s1, s2, key=lambda s: len(s)))

main()

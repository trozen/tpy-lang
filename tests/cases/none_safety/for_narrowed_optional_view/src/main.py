# A for-loop / comprehension over a narrowed `str | None` / `bytes | None`
# (proven non-None) iterates the contained value.


def sum_bytes(b: bytes | None) -> int:
    if b is None:
        return -1
    acc = 0
    for x in b:
        acc += int(x)
    return acc


def sum_chars(s: str | None) -> int:
    if s is None:
        return -1
    acc = 0
    for c in s:
        acc += ord(c)
    return acc


def comp_bytes(b: bytes | None) -> int:
    if b is None:
        return -1
    return sum([int(x) for x in b])      # list comprehension over the narrowed iterable


def distinct_chars(s: str | None) -> int:
    if s is None:
        return -1
    return len({c for c in s})           # set comprehension


def byte_map(b: bytes | None) -> int:
    if b is None:
        return -1
    return len({x: int(x) for x in b})   # dict comprehension


def sum_list(xs: list[int] | None) -> int:
    # Inverse: a narrowed pointer-repr Optional must keep iterating correctly
    # (the value-Optional deref must NOT over-trigger here).
    if xs is None:
        return -1
    acc = 0
    for v in xs:
        acc += v
    return acc


def main() -> None:
    print(sum_bytes(b"ab" + b"c"), sum_bytes(None))
    print(sum_chars("hello"), sum_chars(None))
    print(comp_bytes(b"ab" + b"c"), comp_bytes(None))
    print(distinct_chars("hello"), distinct_chars(None))
    print(byte_map(b"ab" + b"c"), byte_map(None))
    data: list[int] = [10, 20, 30]
    print(sum_list(data), sum_list(None))


main()

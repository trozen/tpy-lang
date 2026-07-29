# A view-form tuple-unpack target lands in an OWNED str/bytes slot: every sink
# (return, container-literal element, container insert, Own[str] arg, an owned
# accumulator seeded from the target) needs the view->owned copy. Both view
# families reach every sink; covers the standalone and for-head unpack forms.
from tpy import Own


def take(s: Own[str]) -> int:
    return len(s)


def first_of(src: tuple[str, str]) -> str:
    x, y = src
    return x


def first_bytes(src: tuple[bytes, bytes]) -> bytes:
    x, y = src
    return x


def take_bytes(b: Own[bytes]) -> int:
    return len(b)


def bytes_elements(src: tuple[bytes, bytes]) -> int:
    x, y = src
    xs: list[bytes] = [x, y]
    xs.append(x)
    return len(xs) + take_bytes(y)


def bytes_accumulate(t: tuple[bytes, bytes]) -> bytes:
    a, b = t
    out = a
    out = out + b
    return out


def elements(src: tuple[str, str]) -> int:
    x, y = src
    # Annotated because two pending-str peers don't unify unannotated
    # (BUGS.md "list literal mixing a str-family LOCAL").
    xs: list[str] = [x, y]
    print(xs[0], xs[1])
    return len(xs)


def own_arg(src: tuple[str, str]) -> int:
    x, y = src
    return take(x)


def accumulate(t: tuple[str, str]) -> str:
    a, b = t
    out = a
    out = out + b
    return out


def collect(pairs: list[tuple[str, int]]) -> int:
    seen: list[str] = []
    for name, num in pairs:
        seen.append(name)
    print(seen[0], seen[1])
    return len(seen)


def main() -> None:
    src = ("ab", "c")
    bsrc = (b"ab", b"c")
    print(first_of(src))
    print(first_bytes(bsrc))
    print(bytes_elements(bsrc))
    print(bytes_accumulate(bsrc))
    print(elements(src))
    print(own_arg(src))
    print(accumulate(src))
    print(collect([("a", 1), ("b", 2)]))


main()

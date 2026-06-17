# A view-form str/bytes source (param, narrowed `T|None` deref, slice) put into
# an owned-element container must be copied into owned storage, not stored as a
# dangling view (regression: these failed the C++ build or moved the view form).


def str_param_sinks(s: str) -> None:
    lit: list[str] = [s]
    app: list[str] = []
    app.append(s)
    st: set[str] = {s}
    d: dict[str, str] = {s: s}
    comp = [s for _ in range(2)]
    set_comp = {s}
    dict_comp = {s: 1 for _ in range(1)}
    print(lit, app, sorted(st), len(d), comp, sorted(set_comp), len(dict_comp))


def str_optional_deref(a: str | None) -> None:
    out: list[str] = []
    if a is not None:
        out.append(a)
        out2: list[str] = [a]
        out.extend(out2)
    print(out)


def str_slice(s: str) -> None:
    out: list[str] = []
    out.append(s[1:4])
    print(out)


def bytes_sinks(b: bytes) -> None:
    # Print lengths/element bytes, not the bytes objects: TPy renders list[bytes]
    # as int lists, which would diverge from CPython's b'...' repr.
    lit: list[bytes] = [b]
    app: list[bytes] = []
    app.append(b)
    app.append(b[1:3])
    print(len(lit), len(app), len(lit[0]), app[0][0], app[1][0], len(app[1]))


def bytes_optional_deref(b: bytes | None) -> None:
    out: list[bytes] = []
    if b is not None:
        out.append(b)
    print(len(out), len(out[0]) if out else 0)


def owned_source_inverse() -> None:
    # An owned str rvalue must keep working: the chokepoint only converts
    # view-form sources, never owned ones (no double-wrap, no perf regression).
    parts: list[str] = []
    parts.append("a" + "b")
    print(parts)


def main() -> None:
    str_param_sinks("hi")
    str_optional_deref("x")
    str_optional_deref(None)
    str_slice("abcdef")
    bytes_sinks(b"hello")
    bytes_optional_deref(b"world")
    bytes_optional_deref(None)
    owned_source_inverse()


main()

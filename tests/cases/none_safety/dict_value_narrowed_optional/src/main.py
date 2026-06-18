# A narrowed str|None / bytes|None param at a dict[k] = v VALUE position must
# deref to the inner value (the value slot is the inner type), like list.append
# does -- it used to pass the whole std::optional and fail the C++ build.


def store_str(a: str | None) -> None:
    out: dict[str, str] = {}
    if a is not None:
        out["k"] = a
        print(len(out), out["k"])
    else:
        print(len(out))


def store_bytes(a: bytes | None) -> None:
    out: dict[str, bytes] = {}
    if a is not None:
        out["k"] = a
    print(len(out), len(out["k"]) if a is not None else 0)


def store_list(a: str | None) -> None:
    # Same fixed path: a list subscript-assign value also unwraps the narrowed
    # optional.
    out: list[str] = ["x"]
    if a is not None:
        out[0] = a
    print(out[0])


def store_optional_value(a: str | None) -> None:
    # Inverse: the value slot is itself Optional -- the whole optional is
    # stored, NOT dereferenced.
    out: dict[str, str | None] = {}
    out["present"] = a
    out["absent"] = None
    print(len(out))


def main() -> None:
    store_str("hello")
    store_str(None)
    store_bytes(b"hi")
    store_list("bound")
    store_optional_value("x")
    store_optional_value(None)


main()

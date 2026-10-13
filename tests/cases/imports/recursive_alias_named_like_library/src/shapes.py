type JsonValue = int | list[JsonValue]


def total(v: JsonValue) -> int:
    if isinstance(v, int):
        return v
    s = 0
    for x in v:
        s += total(x)
    return s


def total_of_nested() -> int:
    nested: JsonValue = [1, [2, 3]]  # tpyc: ok
    return total(nested)

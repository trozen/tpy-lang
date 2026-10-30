# The documented divergence a format spec on a union carries: TPy applies the
# spec to the held value's str() text and warns; CPython formats a held float
# as a number (`3.1` here). `{u!s:.2}` states the text meaning and is silent.


def show(u: float | str) -> None:
    # The warned divergence: TPy prints [3.], CPython [3.1].
    print(f"[{u:.2}]")  # tpyc: warning(/format spec on float \| str pads its str\(\) text; CPython passes it to the value.s __format__/)
    # The explicit text spec: both print [3.].
    print(f"[{u!s:.2}]")  # tpyc: ok


def main() -> None:
    show(3.14159)


main()

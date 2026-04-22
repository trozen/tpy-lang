"""Runtime support for the label macro. tag() prefixes a value with [static] or
[dynamic]; emit() prints fmt plus each tagged part so output reveals which
parts the macro classified as static."""


def tag(kind: str, value: str) -> str:
    return f"[{kind}]{value}"


def emit(fmt: str, parts: list[str]) -> None:
    print(fmt)
    for p in parts:
        print(p)

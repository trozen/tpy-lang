# Annotated str locals resolve to StrView when view-safe (never mutated)

def literal_view() -> None:
    a: str = "hello"  # tpyc: type(StrView)
    print(a)

def literal_promote_augassign() -> None:
    b: str = "hello"  # tpyc: type(str)
    b += " world"
    print(b)

def literal_promote_reassign() -> None:
    c: str = "start"  # tpyc: type(str)
    c = c + " end"
    print(c)

def multiple_views() -> None:
    x: str = "one"  # tpyc: type(StrView)
    y: str = "two"  # tpyc: type(StrView)
    print(x)
    print(y)

def main() -> None:
    literal_view()
    literal_promote_augassign()
    literal_promote_reassign()
    multiple_views()

main()

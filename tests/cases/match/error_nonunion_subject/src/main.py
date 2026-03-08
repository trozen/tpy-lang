# error: match subject must be a supported type (list is not matchable)

def describe(items: list[int]) -> str:
    match items:  # tpyc: error(/must be a union, enum, primitive, record, or Optional type/)
        case _:
            return "something"
    return ""

def main() -> None:
    pass

main()

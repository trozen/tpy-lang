# A temporary at a generic parameter instantiated at a union or Optional is
# held at the slot's type: the storage is the value built from the member.


class Mat:
    hits: int

    def __init__(self) -> None:
        self.hits = 0


class Other:
    def __init__(self) -> None:
        pass


def held[T](x: T) -> str:
    return "held"


def main() -> None:
    # held as the union built from the Mat, not as a bare Mat
    print("generic_union", held[Mat | Other](Mat()))  # tpyc: ok
    # held as the Optional built from the Mat
    print("generic_optional", held[Mat | None](Mat()))  # tpyc: ok


main()

print("class: cls_c init")
names: list[str] = ["a", "b"]


class C:
    def __init__(self) -> None:
        pass

    def add(self, s: str) -> None:
        names.append(s)

    def count(self) -> int:
        return len(names)

# Regression: subscript-LHS receiver (`self.f[i] = v`) on a value-Optional
# container field. After `if self.f is None: return`, sema views self.f as
# the inner container but storage stays std::optional<vector<...>>, so the
# subscript-assign codegen must unwrap before calling __setitem__.
class Buffer:
    items: list[int] | None
    by_key: dict[str, int] | None

    def __init__(
        self,
        items: list[int] | None,
        by_key: dict[str, int] | None,
    ) -> None:
        self.items = items
        self.by_key = by_key

    def step(self) -> None:
        if self.items is None:
            return
        if self.by_key is None:
            return
        self.items[0] = 99
        self.items[1] = self.items[0] + 1
        self.items[2] += 10
        self.by_key["a"] = 7
        self.by_key["b"] = self.by_key["a"] + 1
        self.by_key["a"] += 1

    def show(self) -> None:
        if self.items is None:
            print("none items")
            return
        if self.by_key is None:
            print("none by_key")
            return
        print(self.items[0], self.items[1], self.items[2])
        print(self.by_key["a"], self.by_key["b"])


def main() -> None:
    items: list[int] = [1, 2, 3]
    by_key: dict[str, int] = {"a": 0, "b": 0}
    b = Buffer(items, by_key)
    b.step()
    b.show()
    none_buf = Buffer(None, None)
    none_buf.step()
    print(none_buf.items is None, none_buf.by_key is None)


main()

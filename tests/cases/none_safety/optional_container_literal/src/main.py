# Regression: container literals (`[...]`, `{...}`, `{k: v}`) flowing into
# pointer-repr `Optional[container]` slots used to mis-render at codegen.
# - Local-init: `lst: list[int] | None = [1, 2, 3]` lowered to
#   `vector<T>* lst = vector<T>{...}` (assigning value to pointer).
# - Call-arg: `f({k: v})` for `f: dict[K, V] | None` lowered to
#   `f(&(ordered_map{...}))` (taking address of rvalue).
# Both now hoist the literal into a named slot, then take its address.
from tpy import int32


def take_list(lst: list[int] | None) -> None:
    if lst is not None:
        print(lst[0])
    else:
        print("None list")


def take_dict(d: dict[str, int32] | None) -> None:
    if d is not None:
        print(len(d))
    else:
        print("None dict")


def take_set(s: set[int32] | None) -> None:
    if s is not None:
        print(len(s))
    else:
        print("None set")


def local_init() -> None:
    lst: list[int] | None = [1, 2, 3]
    d: dict[str, int32] | None = {"a": 7}
    s: set[int32] | None = {1}
    if lst is not None and d is not None and s is not None:
        print(lst[0], len(d), len(s))


class Bag:
    items: list[int] | None
    by_key: dict[str, int32] | None

    def __init__(self) -> None:
        self.items = None
        self.by_key = None

    def fill(self) -> None:
        # Post-construction field assignment from a container literal.
        # Field storage is `std::optional<T>` and accepts the value via
        # implicit ctor; covers a distinct code path from local-init.
        self.items = [10, 20]
        self.by_key = {"x": 99}


def main() -> None:
    take_list([1, 2, 3])
    take_dict({"a": 42})
    take_set({1})
    take_list(None)
    take_dict(None)
    take_set(None)
    local_init()
    bag = Bag()
    bag.fill()
    if bag.items is not None and bag.by_key is not None:
        print(bag.items[0], bag.by_key["x"])


main()

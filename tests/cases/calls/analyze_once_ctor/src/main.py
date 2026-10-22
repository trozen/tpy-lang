# A constructor's argument is analyzed once, at a generic record's
# inferred parameters and at an __init__ overload group (OSError's): a
# consuming call inside it is consumed once. A generic construction met
# again at another slot infers its type arguments again there.
from typing import Self
from tpy import Own, dispatch, int32, int64


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self


class E(Exception):
    v: int32

    def __init__(self, v: int32) -> None:
        super().__init__(f"E {v}")
        self.v = v


class Holder[T]:
    item: T

    def __init__(self, item: Own[T]) -> None:
        self.item = item


class Bag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []


class Tagged[K, T]:
    key: K
    items: list[T]

    def __init__(self, key: Own[K]) -> None:
        self.key = key
        self.items = []


def plain_bags(bs: list[Bag[int64]]) -> str:
    bs[0].items.append(5000000000)
    return f"{bs[0].items[0]}"


def plain_holder(h: Holder[Bag[int64]]) -> str:
    h.item.items.append(5000000001)
    return f"{h.item.items[0]}"


@dispatch
def bags(x: str) -> str:
    return "str " + x


@dispatch
def bags(x: list[Bag[int64]]) -> str:
    x[0].items.append(5000000002)
    return f"list {x[0].items[0]}"


@dispatch
def bag_map(x: str) -> str:
    return "str " + x


@dispatch
def bag_map(x: dict[str, Bag[int64]]) -> str:
    x["k"].items.append(5000000003)
    return f"dict {x['k'].items[0]}"


@dispatch
def tagged(x: str) -> str:
    return "str " + x


@dispatch
def tagged(x: list[Tagged[B, int64]]) -> str:
    x[0].items.append(5000000004)
    return f"list {x[0].key.v} {x[0].items[0]}"


class Shelf:
    @dispatch
    def put(self, x: list[Bag[int64]]) -> str:
        x[0].items.append(5000000005)
        return f"list {x[0].items[0]}"

    @dispatch
    def put(self, x: str) -> str:
        return "str " + x


def main() -> None:
    flag = True
    c = B(1)
    print("generic-ctor:", Holder(c.take()).item.v)  # tpyc: ok
    c = B(2)
    print("generic-ctor-field:", Holder(c.take().v).item)  # tpyc: ok
    c = B(3)
    print("group:", OSError(c.take().v, "three").errno)  # tpyc: ok
    c = B(4)
    print("group-fstring:", str(OSError(f"{c.take().v}")))  # tpyc: ok
    c = B(5)
    print("group-select:", OSError(c.take().v if flag else 0, "five").errno)  # tpyc: ok
    # a raised construction receives its argument once, plain and grouped
    c = B(6)
    try:
        raise E(c.take().v)  # tpyc: ok
    except E as e:
        print("raise-plain:", e.v)
    c = B(7)
    try:
        raise OSError(c.take().v, "seven")  # tpyc: ok
    except OSError as e:
        print("raise-group:", e.errno)
    # a construction in a list literal at a typed slot takes its type
    # arguments from the element type
    print("ctor-list-slot:", plain_bags([Bag()]))  # tpyc: ok
    # met at the probe of its Holder's argument, then received there
    print("ctor-nested-slot:", plain_holder(Holder(Bag())))  # tpyc: ok
    # the overload probe meets the construction with no slot (the str
    # candidate first); the winner's element type completes it
    print("ctor-dispatch-list:", bags([Bag()]))  # tpyc: ok
    print("ctor-dispatch-dict:", bag_map({"k": Bag()}))  # tpyc: ok
    # the same construction at the probe and the winner consumes once
    c = B(8)
    print("ctor-dispatch-consume:", tagged([Tagged(c.take())]))  # tpyc: ok
    s = Shelf()
    print("ctor-method-list:", s.put([Bag()]))  # tpyc: ok
    print("ctor-method-keyword:", s.put(x=[Bag()]))  # tpyc: ok


main()

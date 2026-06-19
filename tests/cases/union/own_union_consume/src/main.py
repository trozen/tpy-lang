# Consuming an Own[recursive-union] result without a helper: an annotated
# assignment auto-moves the Own into the union local, and a bare-generic
# isinstance (dict / list) narrows it to the matching member. Mutating the
# narrowed container proves it aliases the owned storage, not a copy.
from tpy import Own

type Json = None | bool | int | str | list[Json] | dict[str, Json]


def build_obj() -> Own[Json]:
    d: dict[str, Json] = {"a": 1, "b": 2}
    return d


def build_arr() -> Own[Json]:
    xs: list[Json] = [1, 2, 3]
    return xs


def main() -> None:
    obj: Json = build_obj()
    if isinstance(obj, dict):
        obj["c"] = 3
        print(len(obj))

    arr: Json = build_arr()
    if isinstance(arr, list):
        arr.append(4)
        print(len(arr))


main()

type V = None | int | str | list[V]
def take(v: V) -> int:
    if not isinstance(v, int):
        return 0
    if not isinstance(v, int):
        return 1
    return v
def use() -> None:
    a: V = 5
    print(take(a))
use()

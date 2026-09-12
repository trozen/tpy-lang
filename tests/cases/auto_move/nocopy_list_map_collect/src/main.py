# Verify that list/dict collect paths move owned values, not copy.
# @nocopy proves at compile time that no hidden copies occur when the
# mapped function returns Own[T] (iterator yields values, not references).
from tpy import int32, Own, nocopy

@nocopy
class Resource:
    val: int32
    def __init__(self, val: int32) -> None:
        self.val = val

def make_resource(x: int32) -> Own[Resource]:
    return Resource(x)

def make_pair(x: int32) -> Own[tuple[str, Resource]]:
    return (str(x), Resource(x))

def main() -> None:
    vals: list[int32] = [int32(1), int32(2), int32(3)]

    # list collect path
    result = list(map(make_resource, vals))  # tpyc: ok
    for r in result:
        print(r.val)

    # dict collect path
    d = dict(map(make_pair, vals))  # tpyc: ok
    for k in d:
        print(k, d[k].val)

main()

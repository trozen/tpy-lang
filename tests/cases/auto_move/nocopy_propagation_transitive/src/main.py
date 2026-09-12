# Transitive propagation: A contains B contains @nocopy C -- A is implicitly nocopy
from tpy import int32, Own, nocopy


@nocopy
class Resource:
    id: int32

    def __init__(self, id: int32):
        self.id = id


class Wrapper:
    res: Resource

    def __init__(self, res: Own[Resource]):
        self.res = res


class Outer:
    w: Wrapper

    def __init__(self, w: Own[Wrapper]):
        self.w = w


def consume(o: Own[Outer]) -> int32:
    return o.w.res.id


def main():
    o = Outer(Wrapper(Resource(99)))
    print(consume(o))  # tpyc: ok


main()

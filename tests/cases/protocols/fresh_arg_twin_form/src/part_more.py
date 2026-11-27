# More fresh-argument sections: a constant-trip comprehension, an async body,
# qualified and unqualified callee / producer spellings, body-less producers.
import asyncio
import os
from os import listdir
from typing import Iterable
from tpy import int32
import fixtures
from fixtures import Acc, bump_twice, total, total_l


def count(xs: Iterable[str]) -> int32:
    n = 0
    for x in xs:
        n += 1
    return n


def const_trip() -> None:
    # constant-trip comprehension at Iterable[int32]; twin: total_l([...])
    # (a vector where an Array local would use fixed storage,
    # BUGS.md#protocol-const-trip-comp-vector)
    print("const comp", total([i for i in range(3)]),  # tpyc: ok
          total_l([i for i in range(3)]))


async def async_body(k: int32) -> int32:
    # async body: a fresh argument on each side of a suspension
    a = total([i for i in range(k)])  # tpyc: ok
    await asyncio.sleep(0)
    b = bump_twice(Acc(k))  # tpyc: ok
    return a + b


def spellings(k: int32) -> None:
    # module-qualified callee: the same inline form as the bare callee
    print("qual callee", fixtures.total(range(k)), total(range(k)))  # tpyc: ok
    print("qual callee comp", fixtures.total([i for i in range(k)]))  # tpyc: ok
    # module-qualified producer: the same form as the bare producer
    print("qual producer", count(os.listdir(".")), count(listdir(".")))  # tpyc: ok
    print("qual producer cond",
          count(os.listdir(".") if k > 0 else ["a"]))  # tpyc: ok
    # a body-less method's fresh container at the protocol parameter, bare
    # and module-qualified callee; twin: total_l(xs.copy()) / total_l(d.pop(3))
    xs = [1, 2, k]
    d = {3: [4, 5], 6: [7], 8: [9]}
    print("copy", total(xs.copy()), fixtures.total(xs.copy()),  # tpyc: ok
          total_l(xs.copy()))
    # the copy is its own list: a later write to the source leaves it alone
    xs.append(10)
    print("copy source", xs)
    print("pop", total(d.pop(3)), fixtures.total(d.pop(6)))  # tpyc: ok
    print("pop source", d)
    # a set literal, a dict literal (iterates its keys) and a split result
    print("set dict", total({1, 2, k}), total({1: 10, 2: 20}),  # tpyc: ok
          fixtures.total({5: 50}))
    print("split", count("a,bb".split(",")),  # tpyc: ok
          count("a,bb,c".split(",")))
    # an operator's and a stepped slice's fresh list
    print("concat slice", total(xs + xs), total(xs[::2]))  # tpyc: ok


def main() -> None:
    const_trip()
    print("async", asyncio.run(async_body(3)))
    spellings(3)

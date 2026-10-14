from typing import Iterator

from tpy import int32, Span, Array

# Test for-each over inferred list (no annotation needed)
items = [1, 2, 3, 4, 5]
total: int32 = 0
for x in items:
    total += x
print(total)  # 15

# Test for-each over Array
arr: Array[int32, 3] = [10, 20, 30]
for val in arr:
    print(val)

# Test for-each with local inferred array (no mutation -> std::array)
def sum_array() -> int32:
    nums = [100, 200, 300]
    result: int32 = 0
    for n in nums:
        result += n
    return result

print(sum_array())  # 600

# Test for-each over Span parameter
def print_span(data: Span[int32]) -> None:
    for x in data:
        print(x)

print_span([7, 8, 9])

# Test for-each over string
text = "AB"
for c in text:
    print(c)

# Test nested for-each
def nested_sum() -> int32:
    outer = [1, 2]
    inner = [10, 20]
    total: int32 = 0
    for a in outer:
        for b in inner:
            total += a * b
    return total

print(nested_sum())  # 1*10 + 1*20 + 2*10 + 2*20 = 90


# Rebinding the iterated name inside the loop: the loop keeps walking the object
# its head evaluated, as in CPython
class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def rebind_iterated() -> None:
    xs = [1, 2, 3]
    total: int32 = 0
    for v in xs:
        total += v
        xs = [100, 200, 300]  # rebinds the name, not the list the loop walks
    nodes = [Node(1), Node(2)]
    # the loop keeps walking the list `keep` still names, not a copy of it
    keep = nodes
    seen: list[int32] = []
    for n in nodes:
        n.v += 10
        seen.append(n.v)
        nodes = [Node(9)]
    print("rebind_iterated", total, xs[0], seen, nodes[0].v, keep[0].v,
          keep[1].v)


def gen_rebind_iterated() -> Iterator[int32]:
    xs = [1, 2, 3]
    for v in xs:
        yield v
        xs = [100, 200, 300]
    yield xs[0]


rebind_iterated()
print("gen_rebind_iterated", list(gen_rebind_iterated()))

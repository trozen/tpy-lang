# basic_slice/slice variables forwarded as subscript index.
# Enables __getitem__ implementations to forward narrowed slice params.
from tpy import int32, Span, basic_slice

def with_basic_slice(items: list[int32], index: int32 | basic_slice) -> int32:
    items.append(0)  # force mutable param
    items.pop()
    if isinstance(index, basic_slice):
        s: Span[int32] = items[index]  # tpyc: ok
        return s[0]
    else:
        return items[index]

def main() -> None:
    items: list[int32] = [10, 20, 30, 40, 50]
    print(with_basic_slice(items, 0))
    print(with_basic_slice(items, 3))

main()

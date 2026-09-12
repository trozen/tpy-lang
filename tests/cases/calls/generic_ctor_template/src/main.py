# Generic constructors with @cpp_template: sema resolves {T}/{K}/{V}/{cpp}
# in the template so codegen receives a fully-resolved string.
from tpy import int32

def main() -> None:
    # list[T] constructor -- {T} resolved to int32_t
    items = list[int32](range(5))
    print(len(items))

    # set[T] constructor -- {T} resolved to int32_t
    s = set[int32](items)
    print(len(s))

    # dict[K,V] constructor -- {K}/{V} resolved
    pairs: list[tuple[str, int32]] = [("a", int32(1)), ("b", int32(2))]
    d = dict[str, int32](pairs)
    print(len(d))

    # Inferred type (no explicit [T]) -- same resolution path
    items2 = list(range(3))
    print(len(items2))

main()

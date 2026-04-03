# Generic constructors with @cpp_template: sema resolves {T}/{K}/{V}/{cpp}
# in the template so codegen receives a fully-resolved string.
from tpy import Int32

def main() -> None:
    # list[T] constructor -- {T} resolved to int32_t
    items = list[Int32](range(5))
    print(len(items))

    # set[T] constructor -- {T} resolved to int32_t
    s = set[Int32](items)
    print(len(s))

    # dict[K,V] constructor -- {K}/{V} resolved
    pairs: list[tuple[str, Int32]] = [("a", Int32(1)), ("b", Int32(2))]
    d = dict[str, Int32](pairs)
    print(len(d))

    # Inferred type (no explicit [T]) -- same resolution path
    items2 = list(range(3))
    print(len(items2))

main()

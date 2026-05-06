# SCC of two cycle modules (a, b) plus a non-cyclic dep (util)
# imported by both. Tarjan partitions: util gets its own
# trivial SCC and runs first; the {a, b} SCC runs as one batch.
# fwd.hpp is emitted only for cycle members; util uses the normal
# .hpp path.
from a import a_func
from b import b_func, b_func_via_a

def main() -> None:
    print(a_func())
    print(b_func())
    print(b_func_via_a())

main()

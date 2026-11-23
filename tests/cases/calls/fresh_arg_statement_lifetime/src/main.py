# A fresh argument (call result, literal, comprehension, conversion) lives for its
# statement: bound inline at every call position, never a named temp.
from fixtures import Bag, P, mk, mkp, total, grow, bump, peek
import part_ctor
import part_positions
import part_union
import part_inverse
import part_opened


def free_fn() -> None:
    # free function: a call result, a literal, a comprehension, `[x] * n`
    print("free", total(mk()), total([1, 2]), total([x for x in range(4)]))  # tpyc: ok
    print("free repeat", total([2] * 3))  # tpyc: ok
    # written parameter: the fresh argument takes the write, bound inline
    print("free written", grow(mk()), grow([5]), bump(P(1)), bump(mkp(7)))  # tpyc: ok
    # nested fresh arguments in one statement
    print("free nested", total(mk()) + grow([total([1, 1])]))  # tpyc: ok


def method() -> None:
    b = Bag([1])
    # method: the same shapes at a method's parameters
    print("method", b.sum_of(mk()), b.sum_of([x * 2 for x in range(3)]))  # tpyc: ok
    print("method written", b.grow_into(mk()), b.grow_into([9, 9]))  # tpyc: ok
    # the receiver's own list is the caller's: a write through it shows
    b.xs.append(2)
    print("method receiver", b.sum_of(b.xs), peek(P(3)))


def main() -> None:
    free_fn()
    method()
    part_ctor.main()
    part_positions.main()
    part_union.main()
    part_inverse.main()
    part_opened.main()


# module-level statement: a fresh argument at a free call, written and read
print("module", total(mk()), grow(mk()))  # tpyc: ok
main()

# A comprehension growing its own source warns as a `for` over it does (warned
# writes never run); the reproducer and module level here, the rest in part_*.py.
from tpy import int32
from fixtures import Cell, grow
import part_bound
import part_enclosing
import part_operands
import part_positions
import part_scope
import part_unlowered


def free_fn(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # free function, the reproducer: a condition hands the source to a
    # mutating callee
    return len([v for v in xs if len(xs) < 5000 and big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/) warning(/may move the elements of 'xs'. but the iteration over 'xs' still walks them/)


def main() -> None:
    print("free_fn:", free_fn([1, 2, 3], False))
    part_positions.main()
    part_enclosing.main()
    part_scope.main()
    part_operands.main()
    part_bound.main()
    part_unlowered.main()


big_module = False
gl = [1, 2, 3]
# module level
gn = len([v for v in gl if big_module and grow(gl)])  # tpyc: warning(/Passing borrowed container 'gl' to non-readonly parameter/)
print("module:", gn)
gcss = [[Cell(1)], [Cell(2)]]
gt = 0
for gc in gcss:
    # module level: a target named like its source's root warns nothing
    gt += sum([gc.bump(1) for gc in gc])  # tpyc: ok
print("module_self_named:", gt, gcss[0][0].v, gcss[1][0].v)

main()

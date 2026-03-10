# break in a for/else else-block is outside a loop
from tpy import Int32

def bad_for_else() -> None:
    items: list[Int32] = [Int32(1)]
    for x in items:
        pass
    else:
        break  # tpyc: error(/'break' outside loop/)

# break in a for/else else-block is outside a loop
from tpy import int32

def bad_for_else() -> None:
    items: list[int32] = [int32(1)]
    for x in items:
        pass
    else:
        break  # tpyc: error(/'break' outside loop/)

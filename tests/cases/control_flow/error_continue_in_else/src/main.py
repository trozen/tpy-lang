# continue in a while/else else-block is outside a loop
from tpy import int32

def bad_while_else() -> None:
    i: int32 = int32(0)
    while i < int32(3):
        i += int32(1)
    else:
        continue  # tpyc: error(/'continue' outside loop/)

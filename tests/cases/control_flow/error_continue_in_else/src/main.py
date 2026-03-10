# continue in a while/else else-block is outside a loop
from tpy import Int32

def bad_while_else() -> None:
    i: Int32 = Int32(0)
    while i < Int32(3):
        i += Int32(1)
    else:
        continue  # tpyc: error(/'continue' outside loop/)

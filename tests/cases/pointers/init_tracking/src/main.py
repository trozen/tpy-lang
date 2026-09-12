from tpy import int32

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32):
        self.x = x
        self.y = y

# Direct assignment then use — OK
def direct_assign() -> None:
    x: Point = Point(7, 8)
    print(x.x, x.y)  # tpyc: ok

# Param usage — OK
def param_use(p: Point) -> None:
    print(p.x, p.y)  # tpyc: ok

# Value type with init — OK
def value_init() -> None:
    x: int32 = 42
    print(x)  # tpyc: ok

# Assign before if, use after — OK
def assign_before_if(cond: bool) -> None:
    x: Point = Point(1, 2)
    if cond:
        x = Point(3, 4)
    print(x.x, x.y)  # tpyc: ok

# Then-branch returns — code after if only reachable from else
def then_returns(cond: bool) -> None:
    if cond:
        return
    x: Point = Point(5, 6)
    print(x.x, x.y)  # tpyc: ok

# Both branches return — dead code after is fine
def both_return(cond: bool) -> int32:
    if cond:
        x: int32 = 1
        return x
    else:
        return 0

# Both branches assign (value type) — OK after if
def both_branches_assign(cond: bool) -> None:
    x: int32
    if cond:
        x = 1
    else:
        x = 2
    print(x)  # tpyc: ok

# Else-branch returns, then assigns — OK after if
def else_returns(cond: bool) -> None:
    x: int32
    if cond:
        x = 10
    else:
        return
    print(x)  # tpyc: ok

# Bare decl then unconditional assign — OK
def decl_then_assign() -> None:
    x: int32
    x = 42
    print(x)  # tpyc: ok

# Loop var shadows assigned outer var — outer stays assigned after loop
def loop_shadow_outer() -> None:
    items: list[int32] = [10, 20, 30]
    x: int32 = 99
    for x in items:
        pass
    print(x)  # tpyc: ok

direct_assign()
param_use(Point(11, 12))
value_init()
assign_before_if(True)
assign_before_if(False)
then_returns(True)
then_returns(False)
print(both_return(True))
print(both_return(False))
both_branches_assign(True)
both_branches_assign(False)
else_returns(True)
else_returns(False)
decl_then_assign()

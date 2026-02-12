from tpy import Int32


class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y


# Value type first declared in both branches
def value_type_branches(cond: bool) -> Int32:
    if cond:
        x: Int32 = 10
    else:
        x = 20
    return x


# One branch declares, other terminates
def else_returns(cond: bool) -> Int32:
    if cond:
        x: Int32 = 42
    else:
        return 0
    return x


# Multiple variables first declared in same if
def multi_var(cond: bool) -> Int32:
    if cond:
        a: Int32 = 1
        b: Int32 = 2
    else:
        a = 3
        b = 4
    return a + b


# Non-value type with rvalue init (needs rebind slot)
def rvalue_branch(cond: bool) -> None:
    if cond:
        p = Point(1, 2)
    else:
        p = Point(3, 4)
    print(p.x, p.y)


# Branch-declared variable reassigned after the if
def reassign_after(cond: bool) -> None:
    if cond:
        x: Int32 = 10
    else:
        x = 20
    x = x + 1
    print(x)


# Nested if — inner if has branch declarations
def nested_if(a: bool, b: bool) -> Int32:
    if a:
        if b:
            x: Int32 = 1
        else:
            x = 2
        y: Int32 = x + 10
    else:
        y = 99
    return y


# Non-value type from param in branches (pointer-local)
def param_branch(points: list[Point], cond: bool) -> None:
    if cond:
        p = points[0]
    else:
        p = points[1]
    print(p.x, p.y)


# Branch-declared non-value type with rvalue in one branch, lvalue in other
def mixed_init(points: list[Point], cond: bool) -> None:
    if cond:
        p = points[0]
    else:
        p = Point(70, 80)
    print(p.x, p.y)


print(value_type_branches(True))
print(value_type_branches(False))
print(else_returns(True))
print(else_returns(False))
print(multi_var(True))
print(multi_var(False))
rvalue_branch(True)
rvalue_branch(False)
reassign_after(True)
reassign_after(False)
print(nested_if(True, True))
print(nested_if(True, False))
print(nested_if(False, True))
pts: list[Point] = [Point(5, 6), Point(7, 8)]
param_branch(pts, True)
param_branch(pts, False)
mixed_init(pts, True)
mixed_init(pts, False)

# Multiple assignment: a = b = c = expr
from tpy import Own

class Obj:
    val: int
    def __init__(self, val: int) -> None:
        self.val = val

class Pair:
    x: int
    y: int
    def __init__(self) -> None:
        self.x = self.y = 0

class Clamped:
    _val: int
    def __init__(self) -> None:
        self._val = 0
    @property
    def val(self) -> int:
        return self._val
    @val.setter
    def val(self, v: int) -> None:
        if v > 100:
            self._val = 100
        else:
            self._val = v

def make_obj(v: int) -> Own[Obj]:
    return Obj(v)

def main() -> None:
    # Value types: three-way
    a = b = c = 10
    print(a, b, c)

    # Value types: expression
    x = y = 2 + 3
    print(x, y)

    # Value types: reassignment
    a = b = 99
    print(a, b, c)

    # Value types: independent after assignment
    p = q = r = 100
    r = 200
    print(p, q, r)

    # Reference type: aliasing (both usable, mutations visible)
    o1 = o2 = Obj(5)
    o1.val = 10
    print(o1.val, o2.val)

    # Object from function call: evaluated once, aliased
    o3 = o4 = make_obj(42)
    o3.val = 0
    print(o3.val, o4.val)

    # Multi-assign in __init__ (self.x = self.y = 0)
    p0 = Pair()
    print(p0.x, p0.y)

    # Mixed: name + field target
    pair = Pair()
    v = pair.x = 77
    print(v, pair.x)

    # Mixed: field + name (name not rightmost in source)
    pair2 = Pair()
    pair2.y = w = 88
    print(pair2.y, w)

    # Two field targets (synthetic temp)
    pair3 = Pair()
    pair3.x = pair3.y = 55
    print(pair3.x, pair3.y)

    # Property: setter called, getter not called, anchor gets raw value
    cl = Clamped()
    n = cl.val = 200
    print(n, cl.val)

    # Property: reversed target order
    cl2 = Clamped()
    cl2.val = m = 200
    print(m, cl2.val)

    # String values
    s1 = s2 = "hello"
    print(s1, s2)

    # List aliasing
    xs = ys = [1, 2, 3]
    xs.append(4)
    print(xs)
    print(ys)

main()

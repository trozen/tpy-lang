# Mutual recursion: union alias references classes that reference back via Box
from tplib import Box
from tpy import Own

type Expr = Lit | BinOp

class Lit:
    value: int
    def __init__(self, value: int) -> None:
        self.value = value

class BinOp:
    left: Box[Expr]
    op: str
    right: Box[Expr]
    def __init__(self, left: Own[Box[Expr]], op: str, right: Own[Box[Expr]]) -> None:
        self.left = left
        self.op = op
        self.right = right

# Return recursive union by value
def make_lit(v: int) -> Own[Expr]:
    return Lit(v)

# Recursive union field in a non-recursive record
class ExprBox:
    expr: Box[Expr]
    def __init__(self, expr: Own[Box[Expr]]) -> None:
        self.expr = expr

def eval_expr(e: Expr) -> int:
    match e:
        case Lit(value=v):
            return v
        case BinOp(left=l, op=op, right=r):
            lv = eval_expr(l.get())
            rv = eval_expr(r.get())
            if op == "+":
                return lv + rv
            else:
                return lv - rv

def main() -> None:
    # simple
    print(eval_expr(Lit(42)))
    # 10 + 3
    e1 = BinOp(Box(Lit(10)), "+", Box(Lit(3)))
    print(eval_expr(e1))
    # (10 + 3) - 1
    e2 = BinOp(Box(BinOp(Box(Lit(10)), "+", Box(Lit(3)))), "-", Box(Lit(1)))
    print(eval_expr(e2))
    # return recursive union by value
    e3 = make_lit(7)
    print(eval_expr(e3))
    # local assigned from member (Lit -> Expr coercion)
    local: Expr = Lit(99)
    print(eval_expr(local))
    # field in non-recursive record
    holder = ExprBox(Box(Lit(55)))
    print(eval_expr(holder.expr.get()))

main()

# Literal-seeded variable (int32 default) promoted to BigInt via
# reassignment inside an if-branch should keep BigInt after the branch.

def get_big() -> int:
    return 42

def test_augassign() -> None:
    x = 0
    if True:
        x = get_big()
    x += 1
    print(x)

def test_binop() -> None:
    x = 0
    if True:
        x = get_big()
    y: int = x + 10
    print(y)

def test_elif_chain() -> None:
    x = 0
    c = '>'
    if c == '>':
        pass
    elif c == '[':
        if True:
            x = get_big()
    elif c == ']':
        if True:
            x = get_big()
    x += 1
    print(x)

test_augassign()
test_binop()
test_elif_chain()

# Literal-seeded variable promoted to BigInt inside an if-branch within
# a while loop should keep BigInt for subsequent operations in the loop body.

def get_big() -> int:
    return 42

def test_augassign_in_while() -> None:
    ip = 0
    while ip < 10:
        c = '>'
        if c == '>':
            pass
        elif c == '[':
            if True:
                ip = get_big()
        elif c == ']':
            if True:
                ip = get_big()
        ip += 1
    print(ip)

def test_binop_in_while() -> None:
    x = 0
    while x < 5:
        if True:
            x = get_big()
        y: int = x + 1
        x = y
    print(x)

test_augassign_in_while()
test_binop_in_while()

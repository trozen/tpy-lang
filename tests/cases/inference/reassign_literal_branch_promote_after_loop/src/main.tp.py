# Literal-seeded variable promoted to BigInt inside a loop should
# keep BigInt for operations after the loop exits.

def get_big() -> int:
    return 42

def test_after_while() -> None:
    x = 0
    while x < 1:
        if True:
            x = get_big()
        x += 1
    y: int = x + 1
    print(y)

def test_after_for() -> None:
    x = 0
    for i in range(0, 3):
        if True:
            x = get_big()
    y: int = x + 1
    print(y)

test_after_while()
test_after_for()

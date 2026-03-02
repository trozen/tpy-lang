# Tuple unpacking reassignment inside a loop (variables declared before loop)
def main() -> None:
    x, y = 0.0, 0.0
    for i in range(3):
        x, y = x + 1.0, y + 2.0
    print(x)
    print(y)

main()

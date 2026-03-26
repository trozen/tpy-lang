# Verify that own_iter() warns when the argument is not at last use.
from tpy import Int32, own_iter

def main() -> None:
    items: list[Int32] = [1, 2, 3]
    for x in own_iter(items):  # tpyc: warning(/own_iter\(\) consumes/)
        print(x)
    print(len(items))

main()

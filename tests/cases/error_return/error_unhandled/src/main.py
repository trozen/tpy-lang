# Error: calling @error_return function without try/except
from tpy import Int32, error_return, ControlFlow

class NotFound(Exception, ControlFlow):
    pass

@error_return(NotFound)
def find(items: list[Int32], target: Int32) -> Int32:
    for i in range(len(items)):
        if items[i] == target:
            return i
    raise NotFound

def main() -> None:
    items: list[Int32] = [10, 20, 30]
    idx = find(items, 20)  # tpyc: error(/must be handled/)
    print(idx)

main()

# all()/any() reject types that don't satisfy Truthy

class Box:
    def __init__(self, x: int) -> None:
        self.x = x

def main() -> None:
    items = [Box(1), Box(2)]
    print(all(items))  # tpyc: error(/does not satisfy 'Truthy'/)

main()

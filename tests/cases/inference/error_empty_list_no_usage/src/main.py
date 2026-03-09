# Empty list with no usage that could infer the element type
def test() -> None:
    xs = []  # tpyc: error(/Cannot infer element type/)
    print(len(xs))

test()

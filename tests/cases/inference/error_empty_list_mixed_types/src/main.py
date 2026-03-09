# Appending incompatible types to an empty list should error
def test() -> None:
    xs = []
    xs.append(1)
    xs.append("hello")  # tpyc: error(/Type mismatch/)
    print(xs)

test()

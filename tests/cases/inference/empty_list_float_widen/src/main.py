# Empty list int-to-float widening: append(int) then append(float) widens to list[float]
def test() -> None:
    xs = []  # tpyc: type(list[float])
    xs.append(1)
    xs.append(2.0)
    print(xs)

test()

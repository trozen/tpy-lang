# An empty list learns list[float] from its uses once the int element is
# converted (control_flow/error_usage_int_float_mix refuses the raw int).
def test() -> None:
    xs = []  # tpyc: type(list[float])
    # The explicit float() is what lets the float element join.
    xs.append(float(1))
    xs.append(2.0)
    print(xs)

test()

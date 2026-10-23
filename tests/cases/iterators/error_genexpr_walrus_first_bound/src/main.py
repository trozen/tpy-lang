# A generator expression's walrus binds the enclosing function's `hit` (PEP
# 572), so that function must bind it first: the frame may never run.
hit = 100


def main(xs: list[int]) -> None:
    # The subject: `hit` is main's local, unbound where the frame takes it.
    found = any((hit := x) > 1 for x in xs)  # tpyc: error(/variable 'hit' may not be assigned at this point/)
    print(found, hit)


main([1, 2])

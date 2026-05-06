import a

print("side effect")  # tpyc: error(/Cyclic import members may only contain imports/)

def bar() -> int:
    return 0

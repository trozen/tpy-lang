def add_offset(x: float | None) -> float:
    return x + 1.0  # tpyc: warning(/Potential None access/)


print(add_offset(1.5))
print(add_offset(None))

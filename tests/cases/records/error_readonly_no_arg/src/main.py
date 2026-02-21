# @readonly() with empty parens is rejected (use bare @readonly or @readonly(False)).
@readonly()  # tpyc: error(/@readonly\(\) requires a single bool argument/)
def bad() -> int:
    return 0

print(0)

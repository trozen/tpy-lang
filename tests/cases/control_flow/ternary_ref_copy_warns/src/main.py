# A reference-type local bound from a ternary with MIXED arm value
# categories (one lvalue arm, one fresh rvalue arm) copies the lvalue arm where
# CPython would alias it. The binding takes one C++ shape and codegen picks the
# copying prvalue form, so this is a silent CPython divergence -- now warned
# (copy() acknowledges it). The warning (comp phase) is the real assertion; the
# runtime output stays parity-clean (len(c) is 4 either way) since the copy
# itself is the acknowledged divergence we are surfacing, not asserting.
def mixed(flag: bool) -> int:
    a = [1, 2, 3]
    c = a if flag else [9]   # tpyc: warning(/ternary copies a reference type/)
    c.append(7)
    return len(c)            # 4 in both TPy (copy) and CPython (alias)


# A both-lvalue ternary ALIASES (codegen binds a reference) and does NOT warn;
# observe the alias by mutating through it.
def both_lvalue(flag: bool) -> int:
    a = [1, 2, 3]
    b = [5, 6]
    c = a if flag else b     # tpyc: ok
    c.append(7)
    return len(a)            # 4 -- c aliased a, mutation visible


def main() -> None:
    print(mixed(True))
    print(both_lvalue(True))


main()

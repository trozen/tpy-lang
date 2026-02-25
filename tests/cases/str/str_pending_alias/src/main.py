# Test PendingStrType alias: each alias gets independent promotion
def test_alias_augassign() -> None:
    s1 = "hello"  # tpyc: type(StrView)
    s2 = s1  # tpyc: type(str)
    s2 += " world"
    print(s1)   # s1 stays StrView (literal, no mutation)
    print(s2)   # s2 promoted to str (augmented assignment)

def test_alias_stays_view() -> None:
    s1 = "hello"  # tpyc: type(StrView)
    s2 = s1  # tpyc: type(StrView)
    print(s1)
    print(s2)

def test_chain_alias_promote() -> None:
    a = "x"  # tpyc: type(StrView)
    b = a  # tpyc: type(str)
    c = b  # tpyc: type(str)
    b += " y"
    print(a)   # StrView (literal, no mutation)
    print(b)   # str (augassign promotes b, which promotes c retroactively)
    print(c)   # str (source b resolved to str)

def test_reassign_from_owned_pending(cond: bool) -> None:
    result = "ok"  # tpyc: type(str)
    if cond:
        s = str(123)  # tpyc: type(str)
        result = s   # s resolves to str -> result must be promoted too
    print(result)

def test_reassign_from_owned_pending_return(cond: bool) -> str:
    result = "ok"
    if cond:
        s = str(123)
        result = s
    return result

test_alias_augassign()
test_alias_stays_view()
test_chain_alias_promote()
test_reassign_from_owned_pending(True)
test_reassign_from_owned_pending(False)
print(test_reassign_from_owned_pending_return(True))
print(test_reassign_from_owned_pending_return(False))

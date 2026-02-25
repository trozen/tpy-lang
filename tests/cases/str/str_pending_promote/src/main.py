# Test PendingStrType promotes to str (std::string) for owned-requiring usage
def test_str_constructor() -> None:
    s = str(42)  # tpyc: type(str)
    print(s)

def test_augassign() -> None:
    s = "hello"  # tpyc: type(str)
    s += " world"
    print(s)

def test_reassign_from_owned() -> None:
    s = "start"  # tpyc: type(str)
    s = str(99)
    print(s)

test_str_constructor()
test_augassign()
test_reassign_from_owned()

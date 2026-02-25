# Test PendingStrType promotes to str (std::string) for owned-requiring usage
def test_str_constructor() -> None:
    s = str(42)
    print(s)

def test_augassign() -> None:
    s = "hello"
    s += " world"
    print(s)

def test_reassign_from_owned() -> None:
    s = "start"
    s = str(99)
    print(s)

test_str_constructor()
test_augassign()
test_reassign_from_owned()

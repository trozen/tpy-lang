# Test mixed: some vars stay view, others promote in the same function
def test_mixed() -> None:
    greeting = "hello"     # tpyc: type(StrView)
    name = "world"         # tpyc: type(StrView)
    result = str(42)       # tpyc: type(str)
    print(greeting)
    print(name)
    print(result)

test_mixed()

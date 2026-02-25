# Test mixed: some vars stay view, others promote in the same function
def test_mixed() -> None:
    greeting = "hello"     # stays view (literal only)
    name = "world"         # stays view (literal only)
    result = str(42)       # promotes (str constructor)
    print(greeting)
    print(name)
    print(result)

test_mixed()

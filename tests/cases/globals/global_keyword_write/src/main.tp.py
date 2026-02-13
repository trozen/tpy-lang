x: int = 0

def increment() -> None:
    global x
    x = x + 1

increment()
increment()
print(x)

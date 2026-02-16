x: int = 0

def update(val: int) -> None:
    global x
    if val > 0:
        x = val
    else:
        x = 0

update(42)
print(x)
update(-1)
print(x)

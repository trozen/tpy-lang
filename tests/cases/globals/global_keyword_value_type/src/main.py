from tpy import int32

counter: int32 = 0
flag: bool = False

def bump() -> None:
    global counter
    global flag
    counter = counter + int32(1)
    flag = True

bump()
bump()
bump()
print(counter)
print(flag)

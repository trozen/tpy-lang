from tpy import Int32

counter: Int32 = 0
flag: bool = False

def bump() -> None:
    global counter
    global flag
    counter = counter + Int32(1)
    flag = True

bump()
bump()
bump()
print(counter)
print(flag)

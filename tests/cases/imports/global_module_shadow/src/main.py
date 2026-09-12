import time
from tpy import int32

class Timer:
    x: int32

time: Timer = Timer()
time.x = 99

def f():
    print(time.x)

f()

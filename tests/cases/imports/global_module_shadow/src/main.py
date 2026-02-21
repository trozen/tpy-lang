import time
from tpy import Int32

class Timer:
    x: Int32

time: Timer = Timer()
time.x = 99

def f():
    print(time.x)

f()

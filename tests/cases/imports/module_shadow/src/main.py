import time
from tpy import int32

class Timer:
    x: int32

def main():
    time: Timer = Timer()
    time.x = 42
    print(time.x)

main()

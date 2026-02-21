import time
from tpy import Int32

class Timer:
    x: Int32

def main():
    time: Timer = Timer()
    time.x = 42
    print(time.x)

main()

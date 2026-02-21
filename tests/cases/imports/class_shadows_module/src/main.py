import time

class time:
    value: int

    def __init__(self, v: int):
        self.value = v

def main():
    t = time(99)
    print(t.value)

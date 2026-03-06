# src/move/fibonacci.py
def fib(n: int) -> int:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)

def main():
    for i in range(50):
        print(fib(i), end=", ")

main()

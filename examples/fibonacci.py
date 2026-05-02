import sys

def fib(n: int) -> int:
    if n <= 1:
        return n
    return fib(n - 1) + fib(n - 2)

def main():
    iter = int(sys.argv[1]) if len(sys.argv) >= 2 else 20
    for i in range(iter):
        print(fib(i), end=", ")

main()

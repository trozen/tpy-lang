from time import time as get_time

def main():
    # Just verify the alias works - don't print actual time (varies between runs)
    t: float = get_time()
    if t > 0.0:
        print("time alias works")

main()

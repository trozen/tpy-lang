import sys

def main():
    # sys.argv should at least contain the program name
    if len(sys.argv) >= 1:
        print("ok")
    else:
        print("error: sys.argv is empty")

main()

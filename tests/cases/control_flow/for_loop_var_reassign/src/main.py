# Test reassigning for-loop variable inside loop body
def main() -> None:
    for i in range(5):
        print(i)
        i = i + 100
    print("done")

main()

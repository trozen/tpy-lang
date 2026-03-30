# Error: bare raise outside except block
def main() -> None:
    raise  # tpyc: error(/bare 'raise' is only valid inside an 'except' block/)

main()

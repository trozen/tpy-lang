from math import custom_add, MAGIC  # tpyc: warning(/shadows builtin module/)

def main():
    result = custom_add(MAGIC, 8)
    print(result)

main()

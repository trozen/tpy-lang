# TurboPython

A proof-of-concept compiler that translates Python to C++.

## Dependencies

Install GMP (GNU Multiple Precision Arithmetic Library):

**Ubuntu/Debian:**

```bash
sudo apt install libgmp-dev
```

**macOS:**

```bash
brew install gmp
```

For Apple Silicon Macs, add these to your shell profile (`.zshrc` or `.bashrc`):

```bash
export CPLUS_INCLUDE_PATH="/opt/homebrew/include:$CPLUS_INCLUDE_PATH"
export LIBRARY_PATH="/opt/homebrew/lib:$LIBRARY_PATH"
```

For Intel Macs, use `/usr/local` instead of `/opt/homebrew`.

## Quick Start

```bash
# Install
uv sync

# Compile and run a program
tpyc -x examples/hello.tp.py

# Release build (optimized)
tpyc -xO examples/hello.tp.py

# Interactive REPL
tpyc -i
```

## CLI Reference

```
tpyc [options] <file.py>

Options:
  -x, --exec        Build and run the program
  -b, --build       Compile C++ to binary
  -O, --release     Build with optimizations (default: debug)
  -i, --repl        Start interactive REPL
  -o <dir>          Output directory (default: __tpyc__/ next to source)
  -v                Verbose output
  -vv               Show compilation commands
```

## Testing

```bash
# Run all tests
uv run pytest -n auto

# Run tests for a specific case
uv run pytest -k hello -n auto
```

## View library documentation

For pretty printing:

```
tpyc --dump-types | glow -p
```

## Manual Build

If you need to compile the generated C++ manually:

```bash
tpyc examples/hello.tp.py -o out/
g++ -std=c++23 -I runtime -o out/program out/hello.d/hello.cpp -lgmp
./out/program
```

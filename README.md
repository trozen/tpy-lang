# TurboPython

A proof-of-concept compiler that translates restricted Python-syntax to C++ for ultra-low-latency applications.

## Quick Start

```bash
# Install
uv sync

# Compile
tpyc examples/example.tp.py -o out/

# Build and run
g++ -std=c++17 -I runtime -o out/program out/generated.cpp
./out/program
```

## Testing

```bash
pytest
```

#pragma once
#include <cstdint>

// Renamed at the C++ side: the Python class binding to this struct uses a
// different name, so `using <Base>::<Base>;` for an empty subclass has to
// derive the constructor name from the C++ side, not the Python class name.
struct CppCounter {
    int32_t value;
    CppCounter() : value(0) {}
    explicit CppCounter(int32_t v) : value(v) {}
    int32_t get() const { return value; }
};

#pragma once
#include <cstdint>
#include <vector>

// A borrow handle into a Buffer's storage.
struct Cursor {
    const int32_t* p;
    int32_t get() const { return *p; }
};

struct Buffer {
    int32_t value = 7;
    Cursor cursor() const { return Cursor{&value}; }
};

// Storage template: owns its elements, so a const element is ill-formed.
template <typename T>
struct Bag {
    std::vector<T> items;
    bool empty() const { return items.empty(); }
};

// Borrowing template: a handle over readonly elements is the const one.
template <typename T>
struct Window {
    T* p = nullptr;
    bool empty() const { return p == nullptr; }
};

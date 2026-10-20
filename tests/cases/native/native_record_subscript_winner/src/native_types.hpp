#pragma once
#include <cstdint>
namespace nb {
struct Item { int32_t n = 64; };
struct Item2 { int32_t n = 32; };
struct Bag {
    Item it;
    Item2 it2;
    Item& b(int64_t) { return it; }
    Item2& a(int32_t) { return it2; }
};
}

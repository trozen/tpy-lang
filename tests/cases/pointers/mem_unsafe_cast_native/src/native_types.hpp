#pragma once
#include <cstdint>

struct thing_t {
    int32_t id;
};

struct sector_t {
    int32_t floor_height;
    void* thinglist;
};

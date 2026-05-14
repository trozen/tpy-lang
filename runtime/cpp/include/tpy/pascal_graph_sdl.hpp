// Pascal Graph SDL2 display layer.
//
// Optional companion to the pure-TPy Pascal Graph unit. The Pascal-side
// `uses GraphSDL;` triggers compilation of this header (via the
// `# tpy: include("<tpy/pascal_graph_sdl.hpp>")` directive in
// `pascal/lib/graph_sdl.py`) plus a `-lSDL2` system link. Tests that
// don't use GraphSDL never see this header and don't depend on libSDL2.
//
// The Pascal Graph runtime owns the pixel buffer + does all drawing
// (Tier A/B/C primitives are pure TPy). This wrapper is purely a "show
// these pixels in a window and block until the user dismisses it" layer
// -- a single-shot function that handles SDL init, window creation,
// texture upload, event loop, and teardown in one call. Users who need
// finer-grained control (multiple frames, polling) can wait until a
// future milestone defines a multi-step display API.

#pragma once

#include <SDL2/SDL.h>

#include <cstdint>
#include <vector>


namespace tpy::pascal {

// Display a 0xRRGGBB pixel buffer in an SDL2 window, then block until
// the user closes the window or presses any key.
//
// `width`, `height` -- canvas dimensions in pixels.
// `pixels`          -- row-major buffer, length must equal width * height.
//                      Each element is a packed 0xXXRRGGBB value (the
//                      upper byte is ignored to match the SDL_PIXELFORMAT_
//                      RGB888 layout).
//
// The function silently no-ops on any SDL failure -- the Pascal-level
// alternative (the PPM snapshot at CloseGraph) is always written
// regardless, so users still get their output even when the window
// can't open.
inline void sdl_show_pixels(std::int32_t width, std::int32_t height,
                             const std::vector<std::int32_t>& pixels) {
    if (width <= 0 || height <= 0) {
        return;
    }
    if (static_cast<std::size_t>(width) * static_cast<std::size_t>(height)
            != pixels.size()) {
        return;
    }
    if (SDL_Init(SDL_INIT_VIDEO) != 0) {
        return;
    }

    // Scale up by 2x so a 320x200 BGI canvas renders at 640x400
    // (closer to readable on a modern display). The texture stays at
    // the canvas size; SDL's RenderCopy stretches it to the window.
    const int win_w = width * 2;
    const int win_h = height * 2;

    SDL_Window* win = SDL_CreateWindow(
        "TurboPython Pascal Graph",
        SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED,
        win_w, win_h, SDL_WINDOW_SHOWN);
    if (win == nullptr) {
        SDL_Quit();
        return;
    }
    SDL_Renderer* ren = SDL_CreateRenderer(win, -1, 0);
    if (ren == nullptr) {
        SDL_DestroyWindow(win);
        SDL_Quit();
        return;
    }
    SDL_Texture* tex = SDL_CreateTexture(
        ren, SDL_PIXELFORMAT_RGB888,
        SDL_TEXTUREACCESS_STREAMING, width, height);
    if (tex == nullptr) {
        SDL_DestroyRenderer(ren);
        SDL_DestroyWindow(win);
        SDL_Quit();
        return;
    }

    // The pixel buffer is std::int32_t per pixel, packed 0xRRGGBB. SDL's
    // PIXELFORMAT_RGB888 expects 4 bytes per pixel with the high byte
    // ignored, which matches the int32 storage layout 1:1 on little-
    // endian hosts (the Pascal POC's target).
    SDL_UpdateTexture(tex, nullptr, pixels.data(),
                       static_cast<int>(width * sizeof(std::int32_t)));
    SDL_RenderClear(ren);
    SDL_RenderCopy(ren, tex, nullptr, nullptr);
    SDL_RenderPresent(ren);

    // Block until quit / any keypress / window close. Mirrors the
    // typical TP7 idiom of `ReadKey; CloseGraph;` at the end of a
    // demo program.
    SDL_Event ev;
    bool running = true;
    while (running && SDL_WaitEvent(&ev) != 0) {
        if (ev.type == SDL_QUIT) {
            running = false;
        } else if (ev.type == SDL_KEYDOWN) {
            running = false;
        } else if (ev.type == SDL_WINDOWEVENT
                   && ev.window.event == SDL_WINDOWEVENT_CLOSE) {
            running = false;
        }
    }

    SDL_DestroyTexture(tex);
    SDL_DestroyRenderer(ren);
    SDL_DestroyWindow(win);
    SDL_Quit();
}

}  // namespace tpy::pascal

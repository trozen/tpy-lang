{ Manual demo for the SDL2 display path of the Graph unit. NOT a
  pytest case -- the regular test suite must stay free of system
  SDL2 dependency. Run locally with libsdl2-dev installed:

      sudo apt install libsdl2-dev   (or your distro's equivalent)
      uv run tpy --dsl-plugin frontends/pascal/pascal_frontend.py \
                 frontends/pascal/sdl_demo/main.pas

  CloseGraph opens a window with the canvas; press any key or
  close the window to exit. The same drawing also writes
  `out.ppm` in the cwd, so users without SDL2 (or with
  --dsl-opt pascal.sdl=off) still get a viewable snapshot. }
program SDLDemo;
uses Graph;
const
  Red = 4;
  Green = 2;
  Yellow = 14;
  White = 15;
  Blue = 1;
  Cyan = 3;
  LightBlue = 9;
var
  gd, gm: integer;
begin
  gd := 0;
  { Explicit 320x200 canvas via the mode = width*1000 + height
    smuggle. With `gm := 0` the BGI Detect path picks VGAHi
    (640x480, matching TP7 on a VGA card), which is too big for
    this hello-house scene. }
  gm := 320200;
  initgraph(gd, gm, '');

  setcolor(LightBlue);
  bar(0, 0, getmaxx(), getmaxy());

  setcolor(Yellow);
  fillellipse(60, 60, 25, 25);    { sun }

  setcolor(Green);
  bar(0, 150, getmaxx(), getmaxy());  { ground }

  setcolor(Red);
  rectangle(100, 100, 200, 150);   { house body }
  setcolor(Yellow);
  bar(101, 101, 199, 149);
  setcolor(Red);
  line(100, 100, 150, 70);
  line(150, 70, 200, 100);          { roof }

  setcolor(White);
  outtextxy(80, 20, 'HELLO BGI');

  closegraph;
end.

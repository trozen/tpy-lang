{ M21: Graph Tier-C primitives. Arc (parametric segment),
  FloodFill (4-way iterative), Bar3D (filled rect + 3D edges),
  SetRGBPalette (override a palette entry), and DOS-driver
  stubs (DetectGraph / RegisterBGIDriver / RegisterBGIFont) that
  always succeed. Tests verify each via GetPixel reads. }
program GraphTierC;
uses Graph;
const
  White = 15;
  Yellow = 14;
  Red = 4;
  Green = 2;
  Cyan = 3;
var
  gd, gm, drv_ok: integer;
begin
  { DOS-driver stubs run and stay out of the way. }
  gd := 0;
  gm := 0;
  detectgraph(gd, gm);
  drv_ok := registerbgidriver(0) + registerbgifont(0);
  writeln('drv_ok=', drv_ok);

  { Standard 80x40 canvas. }
  gm := 80040;
  initgraph(gd, gm, '');

  { Arc from 0 (east) to 90 (north) at center (40, 20) radius 10.
    Should light pixels on the upper-right quadrant only. }
  setcolor(Red);
  arc(40, 20, 0, 90, 10);
  writeln('arc_east@50,20=', getpixel(50, 20));
  writeln('arc_north@40,10=', getpixel(40, 10));
  writeln('arc_south_blank@40,30=', getpixel(40, 30));
  writeln('arc_west_blank@30,20=', getpixel(30, 20));

  { Bar3D: filled bar (5..15, 25..35) with depth=3, top=True.
    The body fills; depth lines extend the rear edge up-right.}
  setcolor(Yellow);
  bar3d(5, 25, 15, 35, 3, true);
  writeln('bar3d_body@10,30=', getpixel(10, 30));
  writeln('bar3d_top_edge@16,24=', getpixel(16, 24));

  { FloodFill: draw a small rectangle outline (60..70, 5..15) in
    Cyan, then seed-fill the interior with Green. Border-color
    argument is Cyan. }
  setcolor(Cyan);
  rectangle(60, 5, 70, 15);
  setcolor(Green);
  floodfill(65, 10, Cyan);
  writeln('flood_interior@65,10=', getpixel(65, 10));
  writeln('flood_interior@67,12=', getpixel(67, 12));
  writeln('flood_border@60,5=', getpixel(60, 5));
  writeln('flood_outside@72,10=', getpixel(72, 10));

  { SetRGBPalette: redefine slot 6 (Brown) as bright white, then
    PutPixel with that index. GetPixel scans the palette by RGB
    so it should now read back as a different index (slot 6 is
    now equal to slot 15 White, so the lookup picks slot 6 first). }
  setrgbpalette(6, 63, 63, 63);
  putpixel(78, 2, 6);
  writeln('rebound@78,2=', getpixel(78, 2));

  closegraph;
  writeln('done');
end.

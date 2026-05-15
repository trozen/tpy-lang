{ M20: Graph Tier-B primitives. Pen state (MoveTo / LineTo /
  GetX / GetY); text rendering via the embedded 8x8 bitmap font
  (OutTextXY / OutText); ellipse outline + fill. Tests use
  GetPixel to verify each primitive lit the expected coords. }
program GraphTierB;
uses Graph;
const
  White = 15;
  Yellow = 14;
  Red = 4;
  Cyan = 3;
  Green = 2;
var
  gd, gm: integer;
begin
  gd := 0;
  gm := 80040;  { 80 x 40 canvas }
  initgraph(gd, gm, '');

  { Pen state: MoveTo + LineTo draw a connected polyline. }
  setcolor(Yellow);
  moveto(2, 2);
  lineto(10, 2);
  lineto(10, 8);
  writeln('penx=', getx(), ' peny=', gety());
  writeln('poly@5,2=', getpixel(5, 2));
  writeln('poly@10,5=', getpixel(10, 5));
  writeln('poly@2,8=', getpixel(2, 8));

  { Text: render 'HI' at (15, 0). The 'H' top-left pixel lands at
    (15, 0); each glyph is 8 wide so 'I' starts at (23, 0). The
    glyph rows are non-blank at row 0 for 'H' and 'I'. }
  setcolor(White);
  outtextxy(15, 0, 'HI');
  { 'H' row 0 is 0x66 -- bits at cols 1,2,5,6. So (16,0) is lit
    and (15,0) is dark. 'I' row 0 is 0x3C -- bits at cols 2..5.
    For 'I' starting at x=23, lit cols are x=25..28. }
  writeln('H_lit@16,0=', getpixel(16, 0));
  writeln('H_dark@15,0=', getpixel(15, 0));
  writeln('I_lit@25,0=', getpixel(25, 0));
  writeln('I_dark@30,0=', getpixel(30, 0));

  { OutText reads the current pen position. After our MoveTo +
    LineTo chain pen is at (10, 8); OutText draws there. }
  setcolor(Cyan);
  outtext('1');
  { '1' row 0 is 0x18 -- bits at cols 3,4. At x=10 -> cols 13,14. }
  writeln('1_lit@13,8=', getpixel(13, 8));

  { Ellipse outline + fill. Outline at (40,20) with rx=10 ry=5;
    the rightmost outline pixel should be at (50, 20). Fill at
    (60, 20) same radii; the center should be lit. }
  setcolor(Red);
  ellipse(40, 20, 0, 360, 10, 5);
  writeln('ell_right@50,20=', getpixel(50, 20));
  writeln('ell_top@40,15=', getpixel(40, 15));
  writeln('ell_center@40,20=', getpixel(40, 20));

  setcolor(Green);
  fillellipse(60, 20, 10, 5);
  writeln('fill_center@60,20=', getpixel(60, 20));
  writeln('fill_right@70,20=', getpixel(70, 20));
  writeln('fill_outside@72,20=', getpixel(72, 20));

  closegraph;
  writeln('done');
end.

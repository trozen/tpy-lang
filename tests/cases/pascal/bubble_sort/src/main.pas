{ M5: array[1..8] of integer, 1-based indexing, nested for loops,
  in-place swap, classic bubble sort. Expected output: ascending. }
program BubbleSort;
var
  data: array[1..8] of integer;
  i, j, tmp: integer;
begin
  data[1] := 5; data[2] := 2; data[3] := 8; data[4] := 1;
  data[5] := 9; data[6] := 3; data[7] := 7; data[8] := 4;
  for i := 1 to 7 do
    for j := 1 to 8 - i do
      if data[j] > data[j + 1] then
      begin
        tmp := data[j];
        data[j] := data[j + 1];
        data[j + 1] := tmp;
      end;
  for i := 1 to 8 do
    writeln(data[i]);
end.

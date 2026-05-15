{ M14.5: array index-site subrange enforcement. Indexing an array
  outside its declared bounds (`array[1..5]`) triggers the runtime
  range check inserted by the translator. }
program PanicArrayIndex;
var
  data: array[1..5] of integer;
begin
  data[1] := 100;
  data[10] := 0;
end.

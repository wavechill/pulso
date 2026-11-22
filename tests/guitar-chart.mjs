import assert from "node:assert/strict";
import { readGuitarChart } from "../web/guitar-chart.js";

// At 120 BPM, tick 192 is half a second. After tick 384, quarter notes
// last a second. The positive Offset means audio has already been playing.
const chart = `[Song]
{
  Resolution = 192
  Offset = 0.125
}
[SyncTrack]
{
  384 = B 60000
  384 = A 1000000
  0 = B 120000
}
[ExpertSingle]
{
  192 = N 4 384
  192 = N 0 0
  192 = N 5 0
  192 = N 6 0
  576 = N 1 0
}
[EasySingle]
{
  0 = N 2 0
}`;
const parsed = readGuitarChart("\ufeff" + chart.replaceAll("\n", "\r\n"));
assert.deepEqual(parsed.tracks, ["Expert", "Easy"]);
assert.deepEqual(parsed.playable("Expert", 5).charts.hard, [
  {t:.625,lane:0,duration:0,chord:true},
  {t:.625,lane:4,duration:1.5,chord:true},
  {t:2.125,lane:1,duration:0}
]);
assert.deepEqual(parsed.playable("Easy", 5).charts.hard, [{t:.125,lane:2,duration:0}]);
assert.deepEqual(parsed.playable("Expert", 5).beats, [.125,.625,1.125,2.125]);
assert.throws(()=>parsed.playable("Expert", 1), /duração/);
assert.throws(()=>parsed.playable("Hard", 5), /dificuldade/);
assert.throws(()=>readGuitarChart(chart.replace("N 4 384","N 7 384")).playable("Expert",5), /abertas/);
assert.throws(()=>readGuitarChart(chart.replace("N 4 384","N 0 1")).playable("Expert",5), /repete/);
assert.throws(()=>readGuitarChart(chart.replace("Resolution = 192","Resolution = 0")), /resolução/);
assert.deepEqual(readGuitarChart(chart.replace("0 = B 120000","")).playable("Expert",5).charts.hard,
  parsed.playable("Expert",5).charts.hard);
assert.equal(readGuitarChart(chart.replace("Offset = 0.125","Offset = -0.2")).playable("Expert",5).charts.hard[0].t,.3);
console.log("Guitarra .chart: acordes, teclas, caudas através de mudanças de BPM, offset, dificuldades e gravação compatível.");

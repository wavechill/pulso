import unittest
import numpy as np

from performances import _playable_windows
from song_phrases import Attack, FPS, _keys, arrange


def fixture(with_voice):
    length=720
    values=np.zeros((length,9),dtype=float)
    pitch=np.full(length,62.)
    confidence=np.zeros(length)
    if with_voice:
        for i,start in enumerate((20,98,171,254,344,428,508,592)):
            values[start:start+56]=42
            pitch[start:start+56]=[61,64,66,64][i%4]
            confidence[start:start+56]=.87
    data=dict(voice_bands=values,mix_voice_bands=values+2,voice_midi=pitch,
              voice_pitch_confidence=confidence,rms=np.full(length,.15),
              drums_flux=np.zeros((length,5)))
    data.update({part+"_level":np.zeros(length) for part in ("lead","bass","drums")})
    chart=dict(duration=length/FPS,bpm=120,beats=list(np.arange(0,length/FPS,.5)),
               charts={m:[] for m in ("easy","medium","hard")})
    return chart,data


class SongPhraseTests(unittest.TestCase):
    def test_a_cappella_is_playable_and_releases_into_the_measured_breaths(self):
        chart=arrange(*fixture(True))
        parent=set()
        for mode in ("easy","medium","hard"):
            notes=chart["charts"][mode]
            self.assertTrue(parent<={n["t"] for n in notes})
            parent={n["t"] for n in notes}
            self.assertEqual(len(notes),8)
            self.assertTrue(all(n["part"]=="voice" and .40<n["duration"]<.75 for n in notes))

    def test_an_empty_stem_creates_no_vocal_or_grid_notes(self):
        chart=arrange(*fixture(False))
        self.assertFalse(any(chart["charts"].values()))

    def test_a_returning_vocal_contour_uses_the_same_fingering(self):
        pitches=(59,61,63,61)
        line=[Attack(t+i*.38,"voice",2,p) for t in (1,8) for i,p in enumerate(pitches)]
        for count in (3,4,5):
            keys=_keys(line,count)
            self.assertEqual(keys[:4],keys[4:])
            self.assertTrue(keys[0]<keys[1]<keys[2] and keys[3]<keys[2])

    def test_a_bass_hold_and_a_coincident_clap_are_both_audible(self):
        sections=[dict(start=0,end=9,role="voice")]
        windows=_playable_windows([dict(t=2,part="voice",accent="snare"),
                                   dict(t=3,part="bass",duration=.7)],sections)
        self.assertEqual([w[2] for w in windows],["voice","drums","bass"])
        self.assertGreater(windows[-1][1],3.7)

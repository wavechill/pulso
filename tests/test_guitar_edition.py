from unittest import TestCase, mock

import numpy as np

from guitar_edition import authored_map, match_clock
from performances import signature
from song_phrases import Attack, _keys
from instrument_focus import family_profile


class GuitarEditionTests(TestCase):
    def test_author_chords_and_releases_follow_the_same_clock_across_tempo_changes(self):
        header="[Song]\n{\nResolution = 192\nOffset = 0.125\n}\n[SyncTrack]\n{\n0 = B 120000\n384 = B 60000\n}\n[Events]\n{\n0 = E \"section Clean Intro\"\n}\n"
        chart=header+"".join("["+key+"Single]\n{\n192 = N 0 384\n192 = N 2 0\n576 = N 1 0\n}\n" for key in ("Easy","Medium","Expert"))
        with mock.patch("guitar_edition.chart_text",return_value=chart):
            c=authored_map(dict(id="test",waveform=[]),(.995,.24),5)
        for rows in c["charts"].values():
            self.assertEqual([n["lane"] for n in rows],[0,2,1])
            self.assertEqual(rows[0]["t"],round(.625*.995+.24,4))
            self.assertAlmostEqual(rows[0]["duration"],1.5*.995,places=4)
            self.assertTrue(rows[1]["chord"])

    def test_a_different_take_cannot_receive_a_named_chart(self):
        rng=np.random.default_rng(6142)
        audio=rng.normal(size=8000*21)
        marks=[dict(time=t,values=np.rint(signature(rng.normal(size=8500))*12)) for t in (2,5,8,11,15,18)]
        self.assertIsNone(match_clock(marks,audio))

    def test_guitar_contour_and_repeated_picks_keep_their_direction(self):
        line=[Attack(i*.23,"lead",1.6,pitch=p) for i,p in enumerate([52,55,55,59,55,52])]
        for count in (3,4,5):
            a,b,c,d,e,f=_keys(line,count)
            self.assertTrue(a<b==c<d and d>e>f)

    def test_a_short_timbre_confusion_cannot_override_a_sustained_piano(self):
        mix=np.tile([0,0,.48,.001,0,.42],(15,1)).astype(float)
        split=mix.copy();split[7]=[.5,0,.02,.38,.29,.02]
        profile=family_profile(mix,split)
        self.assertTrue(np.all(profile[:,2]>profile[:,0]))

import unittest
from copy import deepcopy

from performances import catalog, reviewed_chart
from reductions import refine


def note(t, lane, part, sync=False):
    return {"t":t, "lane":lane, "part":part, "sync":sync}


class PhrasingTests(unittest.TestCase):
    def test_a_single_instrument_really_repeating_stays_on_its_key(self):
        rows = [note(i*.4, 1, "bass") for i in range(7)]
        charts = {"easy":rows[::2], "medium":rows, "hard":deepcopy(rows)}
        original = deepcopy(charts)
        self.assertEqual(refine({"charts": charts, "bpm":120})["charts"], original)

    def test_a_reduction_can_play_the_instrumental_answer_in_the_recording(self):
        drums = [note(i*.4, 0, "kick", i==0) for i in range(8)]
        # The higher difficulty contains a real five-key figure between the kicks.
        answer = [note(i*.4+.08, (1,3,2,4)[i%4], "lead") for i in range(1,7)]
        hard = sorted(deepcopy(drums) + answer, key=lambda n: n["t"])
        chart = {"bpm":120,"charts":{"easy":[drums[0]],"medium":deepcopy(drums),"hard":hard}}
        before = deepcopy(hard)
        refine(chart)
        medium = chart["charts"]["medium"]
        self.assertGreaterEqual(len({n["lane"] for n in medium}),3)
        self.assertEqual({n["t"] for n in medium if n["sync"]}, {0})
        self.assertLessEqual({n["t"] for n in medium}, {n["t"] for n in hard})
        self.assertEqual(hard,before)
        second = deepcopy(chart)
        self.assertEqual(refine(chart),second)

    def test_reviewed_recordings_share_one_clock_for_all_notes_beats_and_sections(self):
        entries = catalog()
        self.assertEqual({item["id"] for item in entries}, {"training","waves","sunflower"})
        for item in entries:
            chart = reviewed_chart((item,.21),item["duration"]+.21)
            for mode,notes in chart["charts"].items():
                self.assertAlmostEqual(notes[0]["t"]-item["map"]["charts"][mode][0]["t"],.21)
                self.assertTrue(all(a["t"]+a.get("duration",0)<=b["t"] for a,b in zip(notes,notes[1:])))
            self.assertAlmostEqual(chart["beats"][0]-item["map"]["beats"][0],.21)
            self.assertAlmostEqual(chart["performance"]["sections"][0]["start"],.21)
            self.assertEqual(refine(chart)["charts"],chart["charts"])


if __name__ == "__main__":
    unittest.main()

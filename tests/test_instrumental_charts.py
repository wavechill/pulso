import unittest

import numpy as np

from analysis_engine import (FPS, _head_times, _instrument_keys, _instrument_map, _instrument_tails, _phrase_heads,
                             _playable_holds, _ringing_bass)
from instrumental import repeating_tones


class InstrumentalChartTests(unittest.TestCase):
    def test_a_short_pick_refines_an_exposed_string_but_a_drum_rest_does_not(self):
        j=45
        flux=np.zeros((100,5))
        flux[j+2,2]=1
        data={"rms":np.ones(100),"lead_level":np.full(100,.7),"drums_level":np.zeros(100),
              "lead_flux":flux,"duration":100/FPS}
        offsets=[np.zeros(100) for _ in range(3)]
        self.assertAlmostEqual(_head_times(data,[j],np.zeros(100,int),offsets)[j],(j+3)/FPS,places=3)
        data["drums_level"][:]=.5
        data["drums_level"][j]=0
        self.assertAlmostEqual(_head_times(data,[j],np.zeros(100,int),offsets)[j],j/FPS,places=3)

    def test_a_strong_in_between_peak_does_not_erase_both_strums(self):
        times = np.array([20, 25, 30])
        rank = np.zeros(46)
        rank[times] = [1.8, 2.1, 1.8]
        self.assertEqual(_phrase_heads(times, rank, 8), [20, 30])

    def test_close_repicks_require_rearticulation_of_the_instrument(self):
        times = np.array([20, 26])
        rank = np.ones(45)
        envelope = np.ones(45)
        lead = np.ones(45,dtype=bool)
        self.assertEqual(len(_phrase_heads(times, rank, 8, (envelope,lead))), 1)
        envelope[23] = .14
        self.assertEqual(_phrase_heads(times, rank, 8, (envelope,lead)), [20, 26])
        lead[26] = False
        self.assertEqual(len(_phrase_heads(times,rank,8,(envelope,lead))),1)

    def test_repeated_pitches_hold_their_key_and_follow_the_riff(self):
        times = np.arange(20, 240, 20)
        pitch = np.zeros(260, dtype=int)
        pitch[times] = [38, 38, 42, 45, 42, 38, 38, 42, 45, 42, 38]
        weight = np.ones(260)
        for lanes in (3, 4, 5):
            keys = _instrument_keys(times,pitch,weight,lanes,44,260)[times]
            np.testing.assert_array_equal(keys[:5], keys[5:10])
            self.assertEqual(keys[0],keys[1])
            self.assertTrue(keys[0] < keys[2] < keys[3])

    def test_instrument_extractor_does_not_invent_strums_from_an_isolated_voice(self):
        frames = 320
        data = {"rms": np.full(frames,.2), "duration":frames/FPS,
                "low_body":np.zeros((frames,25)), "repeats":np.empty((0,3))}
        for p in ("bass","lead","drums"):
            data[p+"_level"] = np.zeros(frames)
            data[p+"_flux"] = np.zeros((frames,5))
            if p != "drums":
                data[p+"_activity"] = np.zeros((frames,88),np.uint8)
                data[p+"_strength"] = np.zeros((frames,2))
                data[p+"_pitch"] = np.zeros((frames,2),int)
        chart = _instrument_map(data)
        self.assertTrue(all(not rows for rows in chart["charts"].values()))
        self.assertEqual(max(chart["waveform"]),1)

    def test_low_synth_spilling_into_accompaniment_starts_with_the_kit(self):
        count, head, end, pitch = 210, 42, 118, 18
        data = {"low_body": np.zeros((count,25)), "rms":np.full(count,.14),
                "lead_level":np.full(count,.06),"bass_level":np.zeros(count),
                "lead_activity":np.zeros((count,88),np.uint8),"bass_activity":np.zeros((count,88),np.uint8)}
        data["lead_activity"][head+3:end,pitch] = 190
        data["low_body"][head:end,pitch-7] = .11
        self.assertEqual(_ringing_bass(data,[head,head+12]),{head:(pitch,end)})

    def test_repick_ends_a_sustain_even_on_a_reduced_difficulty(self):
        activity = np.zeros((200,88),np.uint8)
        activity[20:150,31] = 180
        pitch = np.full(200,31)
        rank = np.ones(200)
        times = np.array([20,83])
        tails = _instrument_tails(times,pitch,activity,np.ones(200),rank)
        self.assertLess(tails[20],83)
        self.assertGreater(tails[20],70)
        self.assertEqual(tails[83],150)

    def test_drum_strikes_route_around_bass_and_chords_stay_playable(self):
        chart = [{"t":0., "lane":0, "part":"bass", "sync":False},
                 {"t":.25, "lane":0, "part":"kick", "sync":False},
                 {"t":.52, "lane":3, "part":"lead", "sync":False},
                 {"t":.52, "lane":0, "part":"lead", "chord":True,"sync":False}]
        _playable_holds(chart,{"bass":{0:95},"lead":{}},"hard")
        self.assertNotEqual(chart[0]["lane"],chart[1]["lane"])
        self.assertAlmostEqual(chart[0]["duration"],.445)
        self.assertNotIn("duration",chart[-1])

    def test_repeated_electronic_plucks_must_have_a_real_attack_at_each_time(self):
        rate, hop = 22050,256
        signal = np.zeros(rate*7,dtype=np.float32)
        t = np.arange(int(.22*rate))/rate
        sound = .3*np.exp(-12*t)*np.minimum(t*450,1)*np.sin(2*np.pi*220*t)
        starts = np.array([.4,.9,1.4,1.9,3.4,3.9,4.4])
        for stamp in starts:
            index = round(stamp*rate)
            signal[index:index+len(t)] += sound
        found = repeating_tones(signal,rate,hop)[:,0]*hop/rate
        self.assertTrue(all(min(abs(found-stamp)) < .04 for stamp in starts))
        self.assertFalse(np.any((found>2.1)&(found<3.2)))


if __name__ == "__main__":
    unittest.main()

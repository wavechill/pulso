---
license: apache-2.0
library_name: onnx
pipeline_tag: audio-classification
tags:
- audio
- audio-classification
- audio-tagging
- audioset
- yamnet
- onnx
---

# YAMNet, ONNX (float16 weights)

YAMNet (tensorflow/models, [`research/audioset/yamnet`](https://github.com/tensorflow/models/tree/c14bf9ad91962cf189f9f58db2132c06247fcd53/research/audioset/yamnet),
by Manoj Plakal and Dan Ellis, Google): a MobileNetV1 trained on AudioSet that scores each 0.96 s log-mel patch for 521
AudioSet classes, a sigmoid each. This is its network over patches, converted for
[@audio/neural-tag](https://github.com/audiojs/neural/tree/main/packages/neural-tag), which computes the patches in
JavaScript as upstream's `features.py` does and runs this file through onnxruntime (Node, or a browser's
onnxruntime-web).

| File | Size | SHA-256 |
|---|---|---|
| `yamnet.fp16.onnx` | 7.5 MB (7,495,175 bytes) | `0a1f3b050ebfdfa651e78fb12714c3c1fe750ceebd58529818a1ddddec515688` |

## Source

- Code: [tensorflow/models](https://github.com/tensorflow/models) at `c14bf9ad91962cf189f9f58db2132c06247fcd53`, the
  last commit to touch `research/audioset/yamnet`: `yamnet.py`, `features.py`, `params.py`, `yamnet_class_map.csv`.
- Weights: [`yamnet.h5`](https://storage.googleapis.com/audioset/yamnet.h5), as upstream's README links it
  (15,296,092 bytes, SHA-256 `13c3308955bbfaef262f175ac9c40e47b134573a93984f009220dd7cc12a1744`).

## Graph

| | name | shape | |
|---|---|---|---|
| input | `patches` | [n, 96, 64] float32 | log-mel patches: 96 frames of 10 ms × 64 mel bands, as `features.py` frames them |
| output | `scores` | [n, 521] float32 | a sigmoid per class, in `yamnet_class_map.csv`'s order |

The patches, at 16 kHz mono (`features.py`, `params.py`): the waveform padded with zeros to at least 15,600 samples and
then to a whole hop; frames of 400 samples every 160, periodic Hann, |FFT 512|; 64 HTK-mel bands 125 to 7500 Hz
(`tf.signal.linear_to_mel_weight_matrix`); ln(mel + 0.001); 96 frames every 48 (0.96 s every 0.48 s).

```js
import { tag } from '@audio/neural-tag'
let tags = await tag(samples, 44100)   // [{ name: 'Speech', id: '/m/09x0r', index: 0, score }, …]
```

## Conversion and verification

`scripts/export.py` in @audio/neural-tag builds the graph from `yamnet.h5`'s arrays (h5py, onnx; opset 17): the 14
convolution layers of `yamnet.py`, each batch norm (center, no scale, epsilon 1e-4) folded into its convolution in
float64, TensorFlow's 'same' padding written out per layer, global average pooling, the dense layer and the sigmoid;
3,729,481 parameters. The weights of over 1024 values are stored as float16 and Cast to float32 in the graph (biases
and the first three kernels stay float32): computed in float32, the file half the float32 export's 14.9 MB.

Against the Keras model (upstream's `yamnet.py` with `yamnet.h5` loaded, TensorFlow 2.21, tf-keras 2.21) on the same 49
patches of seven test signals (speech, plucked notes, a swept tone, white noise, a beep, silence, nothing):

| | max \|score difference\| | mean | top class |
|---|---|---|---|
| float32 export | 1.6e-6 | 1.5e-8 | the same in 49 of 49 |
| this file | 3.6e-3 | 3.0e-6 | the same in 49 of 49 |

End to end, @audio/neural-tag's JavaScript features through this file against upstream's `yamnet_frames_model` on the
same waveform: 2.3e-3 at most on 12.3 s of speech, 3.6e-3 on plucked notes, within 1.4e-3 on the rest; the same top
classes. The features themselves are within 2.4e-7 of upstream's ops computed in float64.

## Accuracy, as upstream states it

On AudioSet's 20,366-segment evaluation set, over the 521 classes: balanced average d-prime 2.318, balanced mAP 0.306,
balanced average lwlrap 0.393 (upstream's README). Six of AudioSet's 527 classes were dropped "on the recommendation of
our Fairness reviewers": the gendered Speech and Singing, Battle cry, Funny music. "YAMNet's classifier outputs have not
been calibrated across classes, so you cannot directly treat the outputs as probabilities" (the TF Hub model card,
Limitations).

## Licence and attribution

Apache License 2.0 ([LICENSE](LICENSE): tensorflow/models' licence at the commit above, which covers `/research`).
YAMNet's code and weights are Google's, "Copyright 2019 The TensorFlow Authors"; the same model on Kaggle (formerly TF
Hub `google/yamnet/1`) states Apache 2.0. Modified from the original weights: converted to ONNX, batch norms folded, the
weights stored as float16. The class names and ids are the AudioSet ontology's, which Google makes available under
CC BY-SA 4.0; AudioSet's labels are CC BY 4.0.

```bibtex
@inproceedings{gemmeke2017audioset,
  title     = {Audio Set: An ontology and human-labeled dataset for audio events},
  author    = {Gemmeke, Jort F. and Ellis, Daniel P. W. and Freedman, Dylan and Jansen, Aren and Lawrence, Wade and Moore, R. Channing and Plakal, Manoj and Ritter, Marvin},
  booktitle = {IEEE International Conference on Acoustics, Speech and Signal Processing (ICASSP)},
  year      = {2017}
}
@article{howard2017mobilenets,
  title   = {MobileNets: Efficient Convolutional Neural Networks for Mobile Vision Applications},
  author  = {Howard, Andrew G. and Zhu, Menglong and Chen, Bo and Kalenichenko, Dmitry and Wang, Weijun and Weyand, Tobias and Andreetto, Marco and Adam, Hartwig},
  journal = {arXiv:1704.04861},
  year    = {2017}
}
```

# EchoPrime Repository Audit

## Scope

This audit is based on the public `EchoPrime` repository cloned on 2026-03-13 at commit `03874a5f203695f38968068c21584656d475b6b1` and on the associated GitHub release assets.

Repo URL: <https://github.com/echonet/EchoPrime>

## Bottom line

`EchoPrime` is best understood as a lightweight inference release with pretrained weights, plus a minimal example of how to load the video and text encoders. It is not a full pretraining or fine-tuning framework for a new echocardiography generative project.

What it clearly supports:

- Study-level echo video embedding from preprocessed clips.
- Coarse view classification for each clip.
- Retrieval-style measurement prediction from a candidate study bank.
- Retrieval-style section-wise report assembly from a candidate report bank.
- Loading pretrained video and text encoders for downstream reuse.

What it does not provide:

- Pretraining code.
- Contrastive training code.
- Fine-tuning loops, losses, configs, or dataset loaders.
- Any generative image or video model.
- A storage-reduction pipeline.

## Repo structure

- `README.md`: inference instructions, release asset locations, notebook pointer.
- `EchoPrimeDemo.ipynb`: example notebook for DICOM processing and inference.
- `echo_prime/model.py`: main `EchoPrime` class and `EchoPrimeTextEncoder`.
- `echo_prime/__init__.py`: re-exports `EchoPrime` and `EchoPrimeTextEncoder`.
- `load_for_finetuning.py`: shows how to load pretrained encoder weights.
- `utils/utils.py`: preprocessing, report phrase handling, view names, masking, video I/O.
- `assets/`: phrase templates, MIL section weights, phenotype mappings, demo image.
- `Dockerfile`: Linux/NVIDIA container path.
- `requirements.txt`: partial dependency list, not a fully reproducible environment.

## Main entry points

### `echo_prime/model.py`

Relevant symbols:

- `EchoPrime.__init__`
- `EchoPrime.process_dicoms`
- `EchoPrime.process_mp4s`
- `EchoPrime.embed_videos`
- `EchoPrime.get_views`
- `EchoPrime.encode_study`
- `EchoPrime.generate_report`
- `EchoPrime.predict_metrics`
- `EchoPrimeTextEncoder`

### `load_for_finetuning.py`

This is the only file that demonstrates how to load the released pretrained encoders outside the full inference object.

## What the released code actually does

### Video encoder

`EchoPrime.__init__` loads a `torchvision.models.video.mvit_v2_s()` backbone, replaces the final head with a 512-dimensional projection, and loads weights from `model_data/weights/echo_prime_encoder.pt`.

Source:

- `echo_prime/model.py`

### View classifier

The same initializer loads a `torchvision.models.convnext_base()` model, swaps its last layer to 11 classes, and loads `model_data/weights/view_classifier.pt`.

The 11 coarse view classes are defined in `utils/utils.py` as:

- `A2C`
- `A3C`
- `A4C`
- `A5C`
- `Apical_Doppler`
- `Doppler_Parasternal_Long`
- `Doppler_Parasternal_Short`
- `Parasternal_Long`
- `Parasternal_Short`
- `SSN`
- `Subcostal`

### Study embedding

`encode_study` concatenates:

- a 512-dimensional video embedding per clip
- an 11-dimensional one-hot coarse view encoding per clip

for a combined per-clip representation of size 523.

### Metric prediction

`predict_metrics` is not a learned regression head. It creates section-weighted study embeddings, retrieves top-k nearest candidate studies from a precomputed candidate bank, and averages stored labels from those retrieved studies.

This makes the released predictor retrieval-based, not end-to-end supervised inference on your input study.

### Report generation

`generate_report` is also retrieval-based. It does not decode free text with a language model. Instead it:

- weights clips by section-specific MIL weights,
- retrieves the nearest candidate study sections,
- extracts the matching section text,
- concatenates sections into a report.

This matters for project planning: EchoPrime is not a generative report model in the repo release. It is a structured nearest-neighbor report assembly system.

## Input assumptions

### DICOM path layout

`process_dicoms(INPUT)` recursively searches `INPUT/**/*.dcm`.

Implication:

- It expects one study folder containing all DICOMs for one echo study.
- It does not consume a manifest directly.
- It does not handle a multi-study root cleanly unless you wrap it yourself.

### MP4 path layout

`process_mp4s(INPUT)` recursively searches `INPUT/**/*.mp4`.

### Tensor shape

The model expects clip tensors shaped:

- `(N, 3, 16, 224, 224)` for batch input.

Internally, preprocessing uses:

- `frames_to_take = 32`
- `frame_stride = 2`

which yields 16 frames after striding from the first 32 frames.

### Preprocessing

The repo preprocesses by:

- masking outside the ultrasound sector (`utils.mask_outside_ultrasound`)
- center crop / aspect correction / resize to `224 x 224` (`utils.crop_and_scale`)
- normalization with fixed dataset mean/std in `EchoPrime.__init__`

### View handling assumptions

View prediction uses only the first frame of each clip:

- `stack_of_first_frames = stack_of_videos[:,:,0,:,:]`

That is a pragmatic coarse-view classifier, not a temporal view model.

### Report/text handling assumptions

Report handling is phrase-template based:

- `assets/all_phr.json`
- `assets/per_section.json`
- `assets/section_to_phenotypes.pkl`

The repo encodes domain knowledge as reusable section phrases and phenotype mappings. This is useful for structured report parsing or weak supervision, but it is not enough for a true text-conditioned generative video model.

## Release assets

GitHub release `v1.0.0` includes:

- `model_data.zip` about 1.34 GB
- `candidate_embeddings_p1.pt` about 1.26 GB
- `candidate_embeddings_p2.pt` about 1.26 GB

Total release payload is about 3.86 GB before extraction overhead.

## Is it inference-only, pretraining-capable, or fine-tuning-capable?

### Inference

Yes, clearly.

### Encoder reuse

Yes, partially.

`load_for_finetuning.py` shows how to load:

- the pretrained video encoder
- the pretrained text encoder

### Fine-tuning

Only in the weak sense that weights can be loaded into PyTorch modules.

What is missing for real fine-tuning:

- dataset class
- batching logic
- loss functions
- optimizer and scheduler setup
- training loop
- validation loop
- checkpointing
- experiment configs
- distributed or mixed-precision logic
- negative sampling or contrastive pairing logic

### Pretraining

No.

There is no released code for the original EchoPrime pretraining recipe.

## Reusable parts for this project

- DICOM-to-clip preprocessing ideas.
- Coarse view classifier weights and labels.
- Video encoder weights as a feature extractor baseline.
- Retrieval-style study embedding as a sanity baseline.
- Phrase and section utilities for structured report parsing.
- Candidate-bank concept for future retrieval baselines.

## What is missing for a true generative echo project

- A clip-level or study-level latent generative model.
- A diffusion, autoregressive, VAE, or masked reconstruction decoder for echo.
- A view-aware echo-specific tokenizer or latent codec.
- Conditioning code for structured measurements or report text.
- A data curation pipeline for paired clip/report/measurement supervision.
- Evaluation code for fidelity, diversity, clinical concept alignment, or downstream synthetic utility.

## Likely failure points out of the box

### 1. Repo-root path assumptions

`utils/utils.py` opens `assets/per_section.json` at import time using a relative path. Importing `echo_prime` outside the repo root fails with `FileNotFoundError`.

### 2. No MPS path

`EchoPrime.__init__` ignores the passed `device` argument and hard-codes:

- CUDA if available
- otherwise CPU

This means Apple Silicon MPS is not used by the released code unless patched.

### 3. `requirements.txt` is not a clean macOS recipe

On Python 3.12, `PyWavelets==1.4.1` fails during build. The file is also missing explicit `torch` and `torchvision` pins, even though the repo requires both.

### 4. Pinned `transformers==4.57.0` is yanked

It still installs, but pip warns that this exact version was yanked.

### 5. Full inference requires large external assets

Instantiating `EchoPrime()` requires not just encoder weights but the full candidate study bank. That increases memory pressure and makes the smallest meaningful test heavier than the README implies.

### 6. DICOM preprocessing may be brittle across vendor/color formats

The masking path assumes YUV/YBR-like color handling in several places. It may not generalize cleanly to all ultrasound DICOM variants without inspection.

### 7. Empty-folder failure mode

If preprocessing finds no usable files, `torch.stack(stack_of_videos)` will fail.

## Assessment for this project

For the MIMIC-IV-ECHO generation project, EchoPrime should be treated as:

- a feature extractor baseline
- a view classifier baseline
- a weak study-level retrieval baseline

It should not be treated as:

- the main preprocessing anchor
- a compression tool
- a generative starting point
- a substitute for a reproducible MIMIC-specific manifest and clip extraction pipeline

## Practical recommendation

Use EchoPrime in this order:

1. Verify encoder-only loading and a forward pass.
2. Use the view classifier as an optional QC / filtering signal for MIMIC clips.
3. Optionally store study- or clip-level embeddings for retrieval baselines.
4. Do not build the core data pipeline around EchoPrime assumptions.
5. Do not postpone a clean raw-to-manifest MIMIC pipeline in favor of forcing EchoPrime to be the dataset processor.

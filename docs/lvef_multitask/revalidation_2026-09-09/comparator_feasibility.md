# Separate comparator feasibility proposal

Status: **documentation/source review only, 2026-09-09**. No model or data was downloaded, no encoder was executed, and no comparator performance was inspected. Finish the existing three-modality ASA revalidation first. This proposal does not change its panel, estimands, hypotheses, or test-release authority.

## Four comparisons with distinct questions

| Candidate | Scientific question | Inputs and outputs | Main feasibility gate | Current disposition |
|---|---|---|---|---|
| Frozen PanEcho features + the same downstream models | Does another pretrained representation improve the same label-completion task under a matched fitting budget? | Same independently locked RGB clips/16-frame membership and subjects; model-specific normalization;768-dimensional feature; deterministic pooling; same Ridge/logistic grids/transforms/masks | Actual compatible pixels; frozen source/checkpoint/task schema/license; pretraining disclosure; extraction adapter and resource pilot | Feasible in principle; no launch |
| Native PanEcho task heads | How does a pretrained multitask prediction system transport to these observed report labels? | Fixed published head outputs and native aggregation/preprocessing; paired supported subjects | Clinical target/unit equivalence; supported modes/views; no unreported local adaptation | Separate system comparison; EF/TAPSE verified candidates; LVOT VTI unsupported by published task list |
| EchoNet-Dynamic EF | How does a task-specific A4C model compare on independently selected A4C studies? | Prespecified A4C clips and model-specific sampling/resolution; EF percent | Blinded A4C selection/quality, checkpoint/config, missing-view denominator, release terms | Feasibility only; cannot apply indiscriminately to all-view C3 inputs |
| Native EchoPrime retrieval/attention | What changes when using the complete pretrained system rather than a mean-pooled encoder? | View-informed section weighting, candidate embeddings/reports/labels and fixed retrieval settings | Candidate-corpus independence and provenance; exact native assets/config/units; license; no evaluation labels in retrieval | Block evaluation until corpus independence is demonstrable |

“Same downstream budget” means identical model classes, candidate counts, train-only transformations, validation rules, seeds, and no primary train+validation refit. It does not imply identical representation size, pretraining information, compute, or acquisition distribution. Native-head inference and a supervised frozen-feature probe answer different questions and must not occupy one undifferentiated leaderboard.

## Primary-source findings

### PanEcho

The official interface exposes a video backbone via `backbone_only=True`, producing 768-dimensional vectors. The normal input is 3×16×224×224 with ImageNet normalization; the authors recommend 16 frames for direct pretrained inference. Native outputs are task-specific probabilities or regression values; study predictions aggregate videos. These establish usable interfaces, not compatibility with the actual C3 retained content. [Official repository](https://github.com/CarDS-Yale/PanEcho).

The published model was developed at Yale using supervised reporting labels across 39 tasks, including 21 regression tasks, from 2D grayscale/color-Doppler videos. Its learned features therefore carry quantitative endpoint supervision before local fitting. The paper excludes still frames, spectral Doppler, strain, and 3D acquisitions from model inputs; native performance on these excluded modes must not be presumed. Publicly reported training provenance does not itself establish patient-level independence from every proposed evaluation source. [Original JAMA study](https://jamanetwork.com/journals/jama/fullarticle/2835630).

The task table explicitly lists `EF` in percent and `TAPSE` in cm. It lists `LVOTDiam` and a categorical `LVOT20mmHg` task, but no LVOT VTI regression head. These names cannot be substituted for `lvot_vti`; TAPSE cm must be reconciled with the locked project unit before a comparison. [Official task schema](https://github.com/CarDS-Yale/PanEcho/blob/main/content/tasks.md).

The loader points to release `v1.0/panecho.pt` and reads `content/tasks.pkl` from the moving main branch. A reproducible runner must pin source, weight digest, and task metadata together; a live `torch.hub` call is not a frozen authority. This review only read the source and release page. [Loader](https://raw.githubusercontent.com/CarDS-Yale/PanEcho/main/hubconf.py), [release](https://github.com/CarDS-Yale/PanEcho/releases/tag/v1.0).

The linked data loader has augmentation/sampling and normalization choices that vary by dataset. C3's normalized EchoPrime tensors must not be reused as PanEcho input: begin with validated RGB pixels and apply the chosen PanEcho transform. For the matched-representation arm, fix identical underlying frames and clip membership for both encoders, document deviation from native sampling, and leave a native-input arm separate. [Author-linked loader revision](https://github.com/CarDS-Yale/PanEcho/blob/34f611db51fd78f6bb1f7a6ef7ab59736afda1d2/src/dataset.py).

No license statement was located in the reviewed README/file inventory; direct `LICENSE` and `LICENSE.md` reads returned404. Public availability does not settle weight/code reuse rights. Record the exact chosen release's terms or owner-provided permission before acquisition/use; do not infer a permissive license from another Yale project. This is an unresolved source check, not a declaration that no license exists anywhere.

### EchoNet-Dynamic

The official project describes 10,030 Stanford A4C videos, cropped/masked outside the ultrasound sector and resized to 112×112. EF/volumes and expert traces are reference annotations. The input domain differs from unrestricted all-view study pooling. [Project/data description](https://echonet.github.io/dynamic/).

The repository supplies EF prediction and segmentation workflows. Before native comparison, pin the actual pretrained EF checkpoint and its evaluated configuration; a default training command or an unrelated reimplementation is not a checkpoint authority. Use independently identified A4C clips, preserve eligible and missing-view counts, and compare every system on the same subjects in this subcohort. View selection must not use EF labels, errors, or model success. [Official repository](https://github.com/echonet/dynamic).

The dataset loader exposes configurable length/period/clip count, uses externally supplied normalization, and handles short clips with post-normalization zero padding. Its constructor defaults and training-command examples are not interchangeable specifications; preserve the chosen checkpoint's exact settings and aggregation. [Official loader](https://raw.githubusercontent.com/echonet/dynamic/master/echonet/datasets/echo.py).

The repository contains both an [MIT LICENSE](https://raw.githubusercontent.com/echonet/dynamic/master/LICENSE) and a restrictive older [LICENSE.txt](https://raw.githubusercontent.com/echonet/dynamic/master/LICENSE.txt). The dataset has separate noncommercial access terms. Resolve which terms govern the selected release and weights before new use or redistribution; do not transfer data or assume software terms grant dataset rights.

### Native EchoPrime

The authors describe contrastive pretraining on more than 12 million video-report pairs, followed by view classification, anatomic attention, and retrieval. That is distinct from the completed local mean-pooled frozen encoder. [Author preprint](https://arxiv.org/abs/2410.09704).

The official model loads candidate study identities, embeddings, reports, and phenotype labels, and its metric prediction uses section-specific similarities and nearest candidates (default `k=50`). Therefore evaluation must establish candidate-patient/study provenance and exclusion of the evaluation cohort, its reports, and labels. A public asset name or mean-pooling result cannot prove independence. [Native model implementation](https://raw.githubusercontent.com/echonet/EchoPrime/main/echo_prime/model.py).

The repository documents model/candidate assets under release v1.0.0. Hash and review the exact chosen assets and output schema before any native run. The README's MIT description conflicts with the current LICENSE file, which sets academic/nonprofit research conditions, limits further transfer, and requires a publication acknowledgment. Preserve the governing chosen-release license and its required acknowledgment rather than quoting the README as license authority. [Asset instructions](https://github.com/echonet/EchoPrime), [actual LICENSE](https://raw.githubusercontent.com/echonet/EchoPrime/main/LICENSE). This observation informs a new comparator proposal; it does not alter the existing completed C3 record.

## Acquisition-independent pilot plan

First resolve the legal/provenance checks, available-pixel inventory, clinical head mapping, and independent input membership. Cache retirement means existing embeddings do not imply existing source pixels. Use only already authorized SCC inputs; no new full-cohort raw transfer or checkpoint acquisition follows from this document.

If separately authorized, pilot at most 20 studies / 200 clips, selected from training/validation by deterministic metadata-only rules, with no performance labels read. Cap each candidate at one GPU and 2 GPU-hours plus boundedCPU work; these are stop limits, not predicted costs. Measure initialization, I/O and inference seconds per clip, peak GPU/host memory, failure counts, output dimensions/dtypes, per-clip stored bytes, checksum reproducibility, and native versus matched-input adapter differences. Stop for unsupported mode/shape, unknown head units, missing corpus provenance, or license uncertainty. No automatic cohort expansion.

Report measured cost before requesting a larger run. For a frozen-feature arm, estimate inference time as validated clip count divided by measured clips/second, with explicit I/O and safety allowance; storage is `N_clips × feature_width × dtype_bytes` plus manifests and study vectors. State that sequential whole-cohort time cannot be inferred from peak GPU throughput alone. Re-pooling existing EchoPrime vectors incurs no new encoder inference but changes a representation and requires its own statistical provenance.

## Analysis and claim boundaries

Use the existing split and support/masking rules, fixed subject pairing, and continuous LVEF primary anchor. Report native task units and cohort coverage for each candidate; do not silently compare A4C-only subjects against a full-study denominator. Freeze model-specific normalization while matching underlying content where the scientific question calls for it. Quantitative supervised pretraining, view availability, and native sampling differences remain explicit confounders of a simplistic architecture claim.

Record when each hypothesis was specified relative to renewed test access. A comparator added after test results is exploratory unless evaluated on a genuinely independent validation source. Do not enlarge the current four-claim Holm family retrospectively, select heads according to test performance, or substitute an easier comparator for an unavailable one. A native EF/TAPSE prediction does not establish direct caliper/trace measurement or clinical benefit.

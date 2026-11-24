Related to #147

- `image_formation.tomography`: per-pixel sparse solvers for a stack of co-registered images (GPU FISTA with a fused
  update kernel and an exact restricted solve inside a height window, batched OMP, reweighted L1, LASSO
  debiasing, SBL with an optional known-noise term, IAA), regularized for coarse height dictionaries, with
  `keep_peaks` to reduce dense spectra to their strongest maxima.
- Pair geometry and elevation wavenumbers along the layover axis (not vertical at squint), the predicted height
  impulse response, and height extraction from a solved stack: strongest atom, weighted mean, highest, or peaks
  along the layover line above a detection floor (`select_extractor` picks from the response width), per-point dB
  levels, an optional prior-DTM height window, and no height (NaN) where there is no return.

## Behavior change
- A new package; nothing on main changes and nothing on main calls it. The multistatic sim app (!783) is its first
  caller.

## Performance (NVIDIA L4)
- FISTA's update runs as one fused kernel: 12 ms -> 5 ms per iteration for 239 atoms x 62,500 pixels, so 600
  iterations take 2.2 s instead of 7.3 s, with scene scores unchanged to 1e-4. On the same scene GPU complex64 FISTA
  takes ~2 s against ~220 s for complex128 numpy, with the same scores.
- Memory: FISTA holds about six (atoms x pixels) complex64 arrays. One million columns ran out of memory on a 23 GB
  L4, so callers chunk the columns (the sim app uses 250 k).
- Converged solves cost far more: 9600 iterations on a 300 x 300 crop took 453-631 s per solve (six jobs sharing
  two L4s).
- SBL, IAA and OMP were not timed on their own; an SBL job on the SFO terminal takes 71-76 s end to end in the sim
  app on one L4.

## Accuracy / validation
- Simulated SFO terminal crop, percent of elevated cells within 2 m of truth for height responses 4 / 8 / 22 m wide:
  FISTA 87.6 / 87.1 / 80.8, IAA 90.9 / 88.7 / 78.2, SBL 92.3 / 90.9 / 71.9, OMP 73.2 / 65.7 / 50.8 (reweighted L1
  and debiasing are also 10-30 points behind FISTA).
- The regularization defaults come from a coarse-dictionary test (22 m response, neighboring atoms correlated
  0.9998): IAA with load 0.1 recovers a single scatterer 99.7 % of the time vs 24-31 % at 1e-4; SBL with noise
  fraction 0.1-1.0 and 300 iterations 94-99 % vs 17 % at 0.01 and 60 iterations.
- Extractor rule: on 72 stored terminal results, the strongest atom instead of the weighted mean for wide responses
  raised the median within-2 m share by 3 points for L1 and 16 for BP (worse on 9 results, by at most 10 points).
- Exact geometry vs a graze-only model differs by up to 0.1 rad/m per pair (2 rad at 20 m height) for formations
  spread 10 km along track; a test pins it.
- 28 tests (in the CPU CI environment 25 pass and 3 GPU-only tests skip). The split stack reproduces all 13 results
  of the earlier branches, 4 exactly and 9 within run-to-run noise.

## Design
- The solvers take a dictionary (pairs x atoms) and a pixel stack (pairs x pixels), batched over pixels, and know
  nothing about SAR acquisition. `build_dictionary` builds the steering from the per-pair wavenumbers that
  `geometry` computes; `response` predicts the height response and picks the extractor; `extraction` turns spectra
  into points.
- The prior window is enforced inside FISTA (an infinite threshold outside the support, an exact restricted LASSO).
  Zeroing atoms of an unrestricted solve was rejected because an aliased replica atom takes part of the energy.
  The other solvers take no support.

## Known limits / follow-ups
- `tomo_fista` defaults to 600 iterations, which is not converged: 2400 iterations raise the atom level by 5.2 dB.
  An open study found that converged FISTA at an NESZ of -6.2 dB loses to BP (9-27 % vs 32-58 % within 2 m); its
  solver changes (window-only FISTA, SBL over the window, a noise-referenced floor) will come as a separate MR.
- The window masks (`column_window_mask`, `line_window_mask`, `window_offsets`) are tested only through the sim
  app's tests in !783.

## Merge order
One of eleven MRs cut from main. !780, !786, !787, !788, !789, !790 and !782 merge in any order; the others merge after the MRs listed:

- !780 viewer3d, targets `main`, merges any time
- !786 ephemeris motions, targets `main`, merges any time
- !787 BP range interpolation, targets `main`, merges any time
- !788 raytrace mesh reuse, batches, targets `main`, merges any time
- !789 sigma0 NESZ noise, targets `main`, merges any time
- !790 GPU-split reference, golden tests, targets `main`, merges any time
- !791 node amplitudes, targets `jisu/mr-sim-gpu-split-reference`, merges after !790
- !782 tomography, targets `main`, merges any time (this MR)
- !783 multistatic sim app, targets `jisu/mr-tomography`, merges after !782, !786, !788, !789, !791
- !784 IPR explorer, targets `jisu/mr-multistatic-sim`, merges after !783, !780
- !785 experimental scripts, targets `jisu/mr-ipr-explorer`, merges after !784, !780
Related to #147

- `sar_common.orbit.ephemeris` reads an external ECEF ephemeris CSV and builds one spotlight motion per
  satellite, pointing at the scene reference point.

## Behavior change
- A new module; nothing existing changes and nothing on main calls it. It reuses main's
  `_local_pointing_from_angles` and `compute_ray_to_earth_intersection`; pandas and scipy are already dependencies.
- The CSV is a time series of ECEF states per body, a different format from the Keplerian-element CSV of
  `sar_common/orbit/formation.py`.
- The multistatic sim app (!783) uses it for formations flown from a real ephemeris.

## Performance
- Not measured. It runs once per collection (cubic splines per body, cached), not per pulse. `save_npz` stores a
  faster-loading copy; that speedup was not timed.

## Accuracy / validation
- 6 tests on a synthetic circular orbit (571 km, a chief and satellites on a relative ellipse): CSV and npz round
  trips (positions to 1e-6 m); spline states at 5 s sampling stay within 1 mm of the orbit radius and 1e-6 m/s in
  speed; the scene reference point lands on the ellipsoid on the look side; every pointing vector aims at it to
  1e-9; 3 satellites give 9 pairs, 6 unique; `relative_rtn` recovers the design ellipse (2 km radial, 4 km along
  track) to 1 m.
- Agreement of an ephemeris-built motion with the analytic helix of the same formation was not measured. The app
  has run on a real ephemeris file (a 4-satellite formation, terminal crop), which shows it works end to end but is
  not a validation of this module.

## Design
- The reference body (`chief`, or the satellites' centroid when there is none) at mid-aperture sets the local frame,
  the look angle sets the boresight, and the scene reference point is its WGS-84 intersection; every satellite then
  spotlights that point.
- It returns all N x N transmit/receive pairs plus the unique ones (tx <= rx) in the convention of the analytic
  formation builders, so downstream pair wavenumbers and BP grids do not care where the motion came from.

## Known limits
- `margin_s` defaults to 0: a caller that needs margin for the round trip adds it.
- Position and velocity are splined independently; their consistency is not enforced.

## Merge order
One of eleven MRs cut from main. !780, !786, !787, !788, !789, !790 and !782 merge in any order; the others merge after the MRs listed:

- !780 viewer3d, targets `main`, merges any time
- !786 ephemeris motions, targets `main`, merges any time (this MR)
- !787 BP range interpolation, targets `main`, merges any time
- !788 raytrace mesh reuse, batches, targets `main`, merges any time
- !789 sigma0 NESZ noise, targets `main`, merges any time
- !790 GPU-split reference, golden tests, targets `main`, merges any time
- !791 node amplitudes, targets `jisu/mr-sim-gpu-split-reference`, merges after !790
- !782 tomography, targets `main`, merges any time
- !783 multistatic sim app, targets `jisu/mr-tomography`, merges after !782, !786, !788, !789, !791
- !784 IPR explorer, targets `jisu/mr-multistatic-sim`, merges after !783, !780
- !785 experimental scripts, targets `jisu/mr-ipr-explorer`, merges after !784, !780
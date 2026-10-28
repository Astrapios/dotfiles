---
name: glb-view
description: Deliver a 3D view (DSM, mesh, point cloud, reconstruction, anything better inspected in 3D than as a flat image) as ECEF-georeferenced GLB files uploaded to the user's S3 prefix with a ready GLB-viewer link. Use when the user asks to "show a 3D view", "see the mesh/DSM/point cloud in 3D", compare reconstructions interactively, or mentions GLB or the glb viewer.
allowed-tools: Read, Bash, Write, Edit, Grep, Glob
---

# 3D / GLB delivery

A 3D request is answered with GLB file(s) in the interactive viewer, not a matplotlib 3D
snapshot. Reply with the link (and a top-down PNG via `astra send-photo` if a quick look helps).

## Steps

1. **Build an ECEF-georeferenced GLB.** `trimesh.Trimesh` with vertices in ECEF, saved through
   `python_common.glb_utils.create_glb_file_for_ecef_mesh(mesh, path)` (stores local ENU
   vertices plus the ECEF node matrix, so float32 precision survives; never put raw ECEF in
   float32 vertices). Weld vertices (`merge_vertices`) before any edge extraction.
   Sanity-check that the mesh center projects to the expected lat/lon before upload.
   A site3d SAR product grid (DOF heights) is in the image frame: layover-correct it first
   (`xy = Ainv @ (-az, -rg) + z * sv` from `sar_common.geometry.geometry.layover_shift`), emit
   small cubes, not a POINTS primitive (1-pixel dots in most viewers), and assert the node
   rotation's determinant is +1 (a mirrored frame point-reflects the scene). Reference:
   `site3d_project_root/test_scripts/glb_export.py` and `glb_verify_geo.py`.
2. **Upload and link** with `python scripts/glb_viewer_link.py --topic <topic> a.glb b.glb`
   (prefix `s3://array-labs-dev-test-data/data/jisu/<topic>/`, default aws profile, viewer
   link printed with every model visible). For a comparison upload each variant as its own GLB
   and list both in one link.
3. **Reply** with the link as Markdown (`[viewer](url)`), what each model is, and the
   altitude range used. The viewer colours by altitude hue (`range=100`) and ignores vertex
   colours; for headless top-down shots pass `cam`, `camrot`, `orbit_point`.

Gotchas (each cost a session): `--profile dev` expires, `--no-sign-request` cannot write (KMS),
the permalink parameter is `models` not `model_paths`, per-triangle vertices fake edges until
welded, SFO GLB class attributes need `pygltflib` (`_CLASS_ID`; trimesh drops them).

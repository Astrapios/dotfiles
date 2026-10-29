---
name: glb-view
description: Deliver a 3D view (DSM, mesh, point cloud, reconstruction, anything better inspected in 3D than as a flat image) as ECEF-georeferenced GLB files uploaded to the user's S3 prefix with a ready viewer link (the IPR explorer viewer by default, the GLB viewer for people off the LAN). Use when the user asks to "show a 3D view", "see the mesh/DSM/point cloud in 3D", compare reconstructions interactively, or mentions GLB or the glb viewer.
allowed-tools: Read, Bash, Write, Edit, Grep, Glob
---

# 3D / GLB delivery

A 3D request is answered with GLB file(s) in the interactive viewer, not a matplotlib 3D
snapshot. Reply with the link (and a top-down PNG via `astra send-photo` if a quick look helps).

The default viewer is the IPR explorer's general 3-D viewer on this box
(`http://10.2.1.59:2720/viewer.html`): it opens every format the GLB viewer reads (GLB, glTF,
LAS, LAZ, CSV, GeoTIFF, depthmap pkl) from `s3://`, `https://` or a path on this box, draws
meshes shaded or as points with a size slider, adds files by box, drag-drop or upload, and
measures with Shift+click. It is on the box's private address: the link opens on the office
network or VPN only, and only while the explorer runs. The GLB viewer
(`https://glb-viewer.dev.arraylabs.net/`, `--viewer glb`) stays the tool for people off the LAN
and for GIF / video exports; `s3://` files open in both.

## Steps

1. **Build an ECEF-georeferenced GLB.** `trimesh.Trimesh` with vertices in ECEF, saved through
   `python_common.glb_utils.create_glb_file_for_ecef_mesh(mesh, path)` (stores local ENU
   vertices plus the ECEF node matrix, so float32 precision survives; never put raw ECEF in
   float32 vertices). Weld vertices (`merge_vertices`) before any edge extraction.
   Sanity-check that the mesh center projects to the expected lat/lon before upload.
   A site3d SAR product grid (DOF heights) is in the image frame: layover-correct it first
   (`xy = Ainv @ (-az, -rg) + z * sv` from `sar_common.geometry.geometry.layover_shift`) and
   assert the node rotation's determinant is +1 (a mirrored frame point-reflects the scene).
   For the GLB viewer emit small cubes, not a POINTS primitive (1-pixel dots there); the
   explorer draws a POINTS primitive with its size slider. Reference:
   `site3d_project_root/test_scripts/glb_export.py` and `glb_verify_geo.py`.
2. **Check the explorer is up** before handing out its link:
   `bash site3d_project_root/site3d_sim/sar_apps/ipr_explorer/serve.sh status` (the link script
   also warns when `/health` does not answer). Do not start or restart it from a worktree; the
   live server runs from the `site3d_sim` checkout (`jisu/sar_apps`).
3. **Upload and link** with `python scripts/glb_viewer_link.py --topic <topic> a.glb b.glb`
   (prefix `s3://array-labs-dev-test-data/data/jisu/<topic>/`, default aws profile, explorer
   link printed with every model visible and labeled by file name; `--labels a,b` to name
   them). `--local outputs/x/a.glb` links files on this box without uploading (explorer only);
   `--viewer glb` prints the GLB-viewer link instead. For a comparison upload each variant as
   its own GLB and list both in one link.
4. **Reply** with the link as Markdown (`[viewer](url)`), what each model is, and how it is
   colored. The explorer colors by height over the data's range (turbo; `--range <m>` gives a
   hue that wraps every that many meters) and offers the file's own colors, texture and scalar
   layers per model; the GLB viewer colors by altitude hue (`range=100`) and ignores vertex
   colors (for its headless top-down shots pass `cam`, `camrot`, `orbit_point`). Explorer camera
   links come from its "copy link" button (`view=az,el,dist,tx,ty[,tz]`).

Gotchas (each cost a session): `--profile dev` expires, `--no-sign-request` cannot write (KMS),
the permalink parameter is `models` not `model_paths`, per-triangle vertices fake edges until
welded, SFO GLB class attributes need `pygltflib` (`_CLASS_ID`; trimesh drops them; the explorer
reads them as `layer:class_id`), explorer links with local paths open only on this box's
explorer.

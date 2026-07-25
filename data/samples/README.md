# Sample data provenance

## Generated (checked into generator, regenerated via `make samples`)

### Synthetic native pair

| File | Description |
|------|-------------|
| `synthetic_native/lift_rev_a.pdf` | Deterministic A3 P&ID-like Rev A |
| `synthetic_native/lift_rev_b.pdf` | Rev B with controlled changes |
| `synthetic_native/ground_truth.json` | Exact labeled changes |

Generator: `scripts/make_synthetic_pid_pair.py`  
Command: `uv run python -c "from scripts.make_synthetic_pid_pair import main; main(seed=42)"`  
Seed: `42`

Controlled changes in Rev B:

1. HH setpoint near `26-PIT-9062`: 245 → 250  
2. Duty table: 12000 → 12500 Nm3/h  
3. Added `NOTE 12`  
4. Removed line tag `4"-PG-1002-A1`  
5. Moved instrument `26-PT-9070` without text change  
6. Added geometry branch + valve `HV-205`  
7. Unchanged anchors include equipment `26-KA-901` and motor kW text  

### Independent secondary native pair

`synthetic_secondary/export_rev_a.pdf` and `export_rev_b.pdf` form a separate
export-gas revision scenario (`DOC-SYN-EXPORT`) with different equipment and
instrument tags. `scripts/make_secondary_pid_pair.py` generates it at seed
`84`; its labeled changes are HH 180 -> 185, duty 8000 -> 8500 Nm3/h, added
`NOTE 20`, and moved `27-PT-1002`. This case prevents evaluation from counting
the primary pair twice.

### Synthetic scanned pair

| File | Description |
|------|-------------|
| `synthetic_scanned/lift_rev_a_scan.pdf` | Image-only scan of Rev A |
| `synthetic_scanned/lift_rev_b_scan.pdf` | Image-only scan of Rev B |
| `synthetic_scanned/ground_truth.json` | Same semantic GT + location tolerance |

Generator: `scripts/make_scanned_pair.py`  
Degradation: skew, mild blur, JPEG artifacts, brightness jitter, speckle  
Native text layer: intentionally empty / negligible (OCR path only)

### Synthetic CAD pair (third format)

| File | Description |
|------|-------------|
| `synthetic_cad/booster_rev_a.dxf` | DXF R2010 Rev A, ISO A3 extents in mm |
| `synthetic_cad/booster_rev_b.dxf` | Rev B with controlled changes |
| `synthetic_cad/ground_truth.json` | Same six-change edit set as the PDF pairs |

Generator: `scripts/make_cad_pair.py`

Both revisions are authored independently from one parameterized spec, so no
superseded entity survives on a hidden layer. Layers (`EQUIPMENT`, `PIPING`,
`INSTRUMENT`, `BORDER`, `TITLE`) are populated because the adapter records them
as element attributes.

Controlled changes in Rev B:

1. HH setpoint near `26-PIT-9080`: 180 → 195
2. Duty table: 8000 → 8600 Nm3/h
3. Added `NOTE 12`
4. Removed line tag `6"-PG-2002-A1`
5. Moved instrument `26-PT-9085` without text change
6. Added geometry branch + valve `HV-305`
7. Unchanged anchors: equipment `26-KA-903`, motor kW, service text

The edit set deliberately mirrors the native and scanned pairs. Comparing
`cad_delta_f1` against `scanned_delta_f1` on the same logical changes separates
*extraction* error from *matching* error: CAD carries exact coordinates, so a
miss there is the delta engine's fault, not OCR's.

No customer drawing was used or derived from in producing these files.

## Private inputs (gitignored)

`data/private_inputs/` may contain the supplied AutoCAD Plant 3D P&IDs for local realism and mismatch testing:

- Lift Gas compressor (`26-KA-901`) → `DOC-LIFT-COMPRESSOR`
- Export Gas Compressor (`26-KA-902`) → `DOC-EXPORT-COMPRESSOR`

These are **not revisions of the same drawing**. They are used only as a pair-mismatch case. Do not redistribute unless authorized.

If private PDFs are absent, `scripts/build_eval_dataset.py` creates tiny mismatch fixtures under `data/samples/mismatch/`.

## Redistribution

- Synthetic samples: free to share (generated in-repo).  
- Private engineering drawings: **not** committed; local only.

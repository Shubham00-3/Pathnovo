"""Ensure registry + eval dataset point at generated samples."""

from __future__ import annotations

import json

from delta_chat.config import project_root


def main() -> None:
    root = project_root()
    reg_path = root / "data" / "registry.json"
    registry = {
        "PID-SYN-A": {
            "underlying_document_id": "DOC-SYN-LIFT",
            "revision_label": "A",
            "path": "data/samples/synthetic_native/lift_rev_a.pdf",
            "media_type": "application/pdf",
            "display_name": "Synthetic Lift Rev A",
        },
        "PID-SYN-B": {
            "underlying_document_id": "DOC-SYN-LIFT",
            "revision_label": "B",
            "path": "data/samples/synthetic_native/lift_rev_b.pdf",
            "media_type": "application/pdf",
            "display_name": "Synthetic Lift Rev B",
        },
        "PID-SYN-SCAN-A": {
            "underlying_document_id": "DOC-SYN-LIFT",
            "revision_label": "A",
            "path": "data/samples/synthetic_scanned/lift_rev_a_scan.pdf",
            "media_type": "application/pdf",
            "display_name": "Synthetic Lift Scan A",
        },
        "PID-SYN-SCAN-B": {
            "underlying_document_id": "DOC-SYN-LIFT",
            "revision_label": "B",
            "path": "data/samples/synthetic_scanned/lift_rev_b_scan.pdf",
            "media_type": "application/pdf",
            "display_name": "Synthetic Lift Scan B",
        },
        "PID-LIFT": {
            "underlying_document_id": "DOC-LIFT-COMPRESSOR",
            "revision_label": "A",
            "path": "data/private_inputs/lift_gas.pdf",
            "media_type": "application/pdf",
            "display_name": "Lift Gas Compressor (private)",
        },
        "PID-EXPORT": {
            "underlying_document_id": "DOC-EXPORT-COMPRESSOR",
            "revision_label": "A",
            "path": "data/private_inputs/export_gas.pdf",
            "media_type": "application/pdf",
            "display_name": "Export Gas Compressor (private)",
        },
    }
    # Keep private entries only if files exist; else use metadata-only mismatch fixtures
    if not (root / "data/private_inputs/lift_gas.pdf").exists():
        # create tiny mismatch fixtures
        fix = root / "data" / "samples" / "mismatch"
        fix.mkdir(parents=True, exist_ok=True)
        import fitz

        for name, tag, docid in [
            ("mismatch_a.pdf", "26-KA-901", "DOC-LIFT-COMPRESSOR"),
            ("mismatch_b.pdf", "26-KA-902", "DOC-EXPORT-COMPRESSOR"),
        ]:
            d = fitz.open()
            p = d.new_page(width=600, height=400)
            p.insert_text((50, 50), f"Equipment {tag}", fontsize=14)
            p.insert_text((50, 80), f"Document {docid}", fontsize=12)
            d.save(str(fix / name))
            d.close()
        registry["PID-LIFT"]["path"] = "data/samples/mismatch/mismatch_a.pdf"
        registry["PID-EXPORT"]["path"] = "data/samples/mismatch/mismatch_b.pdf"

    reg_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    print(f"Wrote {reg_path}")


if __name__ == "__main__":
    main()

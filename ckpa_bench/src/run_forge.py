#!/usr/bin/env python3
"""Run CKPA-Bench V2 — source-span verified benchmark construction.

Three layers:
  Core: DXY clinical decisions full-text → CKPA items (Chinese standard vs LLM prior)
  Drug-label: structured drug CSV label text → CKPA items (safety constraint vs common practice)
  Cross-source: DXY decisions vs AMBOSS international PDFs → real dual-source conflict items

Usage:
    python run_forge.py --mode sanity                  # 3+3+3 fast check
    python run_forge.py --mode mvp                     # 50+50+30 items
    python run_forge.py --mode core --core 75          # Core only
    python run_forge.py --mode drug --drug 75          # Drug-label only
    python run_forge.py --mode combination --drug 30   # Combination only
    python run_forge.py --mode validate --input output/core/ckpa_core_latest.json
"""

import argparse
import json
import logging
import sys

from config import PipelineConfig
from pipeline import CKPAForge

logger = logging.getLogger("run_forge")


def main():
    parser = argparse.ArgumentParser(description="CKPA-Bench V2 Forge")
    parser.add_argument("--mode", default="sanity",
                        choices=["sanity", "mvp", "core", "temporal", "combination", "drug", "validate"])
    parser.add_argument("--core", type=int, default=1000, help="Core + Temporal target items")
    parser.add_argument("--drug", type=int, default=1000, help="Drug-label + Combination target items")
    parser.add_argument("--cross", type=int, default=0, help="(deprecated)")
    parser.add_argument("--max-drugs", type=int, default=3000, help="Max drug rows / decisions to scan")
    parser.add_argument("--input", type=str, default=None, help="Input for validation")
    parser.add_argument("--diseases", nargs="+", default=None,
                        help="Disease keywords")

    args = parser.parse_args()
    cfg = PipelineConfig()
    forge = CKPAForge(cfg)

    all_generated = []
    all_passed = []

    # Load state for crash recovery — skip already-completed layers
    build_state = forge.load_build_state()
    completed_layers = set(build_state.get("completed_layers", []))

    def run_layer(layer_fn, n_items, layer_name, **kw):
        if n_items <= 0:
            return
        if layer_name in completed_layers:
            logger.info("Skipping %s (already completed in previous run)", layer_name)
            return
        items = layer_fn(max_items=n_items, **kw)
        all_generated.extend(items)
        # Validate and immediately append passed items to final
        if items:
            val = forge.validate_items(items, layer_name=layer_name)
            passed = [it for it, r in zip(items, val.get("results", []))
                      if r.get("verdict") == "PASS"]
            all_passed.extend(passed)
            logger.info("%d/%d passed validation", len(passed), len(items))

            # Append to final immediately (crash-safe)
            final_dir = cfg.paths.output_dir / "final"
            final_dir.mkdir(parents=True, exist_ok=True)
            final_path = final_dir / "ckpa_passed.json"
            existing_final = json.loads(final_path.read_text(encoding="utf-8")) if final_path.exists() else []
            existing_final.extend(passed)
            final_path.write_text(json.dumps(existing_final, ensure_ascii=False, indent=2), encoding="utf-8")

            # Save build state for crash recovery
            completed_layers.add(layer_name)
            forge.save_build_state({
                "completed_layers": list(completed_layers),
                "last_layer": layer_name,
                "items_so_far": len(all_passed),
                "timestamp": __import__('datetime').datetime.now().isoformat(),
            })

    if args.mode == "sanity":
        logger.info("=== SANITY CHECK  (3 items per layer, 4 layers) ===")
        run_layer(forge.build_core_from_decisions, 3, "core")
        run_layer(forge.build_temporal_layer, 3, "temporal")
        run_layer(forge.build_drug_label_layer, 3, "drug_label", max_drugs_to_scan=1000)
        run_layer(forge.build_combination_layer, 3, "combination", max_drugs_to_scan=3000)

    elif args.mode == "mvp":
        logger.info("=== MVP BUILD (target: ~130 verified items) ===")
        run_layer(forge.build_core_from_decisions, args.core, "core")
        run_layer(forge.build_temporal_layer, args.core, "temporal")
        run_layer(forge.build_drug_label_layer, args.drug, "drug_label", max_drugs_to_scan=args.max_drugs)
        run_layer(forge.build_combination_layer, args.drug, "combination", max_drugs_to_scan=args.max_drugs)

    elif args.mode == "core":
        run_layer(forge.build_core_from_decisions, args.core, "core")

    elif args.mode == "temporal":
        run_layer(forge.build_temporal_layer, args.core, "temporal")

    elif args.mode == "combination":
        run_layer(forge.build_combination_layer, args.drug, "combination", max_drugs_to_scan=args.max_drugs)

    elif args.mode == "drug":
        run_layer(forge.build_drug_label_layer, args.drug, "drug_label", max_drugs_to_scan=args.max_drugs)

    elif args.mode == "validate":
        if not args.input:
            logger.error("--input required for validate mode")
            sys.exit(1)
        with open(args.input, "r", encoding="utf-8") as f:
            items = json.load(f)
        forge.all_items = items
        val = forge.validate_items(items)
        passed = [it for it, r in zip(items, val.get("results", []))
                  if r.get("verdict") == "PASS"]
        logger.info("Validation: %d/%d passed", len(passed), len(items))

    # Save final passed dataset
    if all_passed:
        out_dir = cfg.paths.output_dir / "final"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "ckpa_passed.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(all_passed, f, ensure_ascii=False, indent=2)
        logger.info("Final dataset: %d validated items saved to %s", len(all_passed), path)

    # Save build manifest with all stats
    manifest = {
        "timestamp": __import__('datetime').datetime.now().isoformat(),
        "mode": args.mode,
        "items_generated": len(all_generated),
        "items_passed": len(all_passed),
        "items_rejected": forge.stats.get("items_rejected", 0),
        "rejection_reasons": forge.stats.get("rejection_reasons", {}),
        "api_calls": forge.client.total_calls,
    }
    manifest_path = cfg.paths.output_dir / f"build_manifest_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    logger.info("Build manifest saved to %s", manifest_path)

    forge.print_stats()


if __name__ == "__main__":
    main()

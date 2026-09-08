# Medical Knowledge-Graph Agent

An evidence-linked medical knowledge graph, a retrieval-grounded answering
layer, and a rubric-based LLM evaluation framework, built for patient questions
about cancer bone metastasis during a 2026 data-science internship.

Consumer AI platforms answer such questions inconsistently: they merge
indications that must stay separate, invent figures and citations, and soften
emergencies into routine advice. Here, authoritative sources become a knowledge
graph with explicit, evidence-backed entities, relationships and safety
constraints; model answers are grounded in evidence retrieved from it; and the
effect is measured by a paired A/B evaluation across six LLM platforms, scored
by an independent judge. Measured failures drive the next round of
construction.

The repository holds code, prompts, schemas and tests only. Knowledge-graph
content, the benchmark, model responses and patient data are excluded.

![Architecture](docs/figures/architecture.svg)

## Components

| Module | Purpose |
| --- | --- |
| [`kb_builder/`](kb_builder/) | Agent skill and scripts: PDF, Word, Excel, OCR and registry exports into one source-traceable KB JSON; citation verification, quality validator, defect-driven optimisation. 61 tests. |
| [`kb_ab_eval/`](kb_ab_eval/) | Paired A/B harness across six platforms: plain prompt vs safety rules plus rubric anchors plus retrieved KB evidence; independent judge; content-addressed, resumable runs. |
| [`model_eval/`](model_eval/) | Multi-provider runner with pooled clients, retries, optional search augmentation, JSON-schema judge scoring and report generation. |
| [`rpa_collection/`](rpa_collection/) | Playwright scripts for platforms with a web UI only, run inside an RPA worker or local Chrome, keeping all models in one time window. |
| [`ckpa_bench/`](ckpa_bench/) | Earlier benchmark pipeline: items where two authoritative sources conflict, testing whether a model arbitrates correctly with and without the sources. |
| [`data_pipeline/`](data_pipeline/) | Two-stage extraction of real patient questions from consultation transcripts, with batched LLM calls and resumable checkpoints. |

## Design

- **Graph.** `entities.jsonl`, `relations.jsonl`, one Markdown document per
  entity. Delivered version: 123 verified nodes across 27 types (`Drug`,
  `Regimen`, `Test`, `AdverseEvent`, `Emergency`, `ClinicalClaim`,
  `ClinicalBoundary`, …) and 161 edges across 44 standard verbs (`regimen_for`,
  `causes_adverse_event`, `requires_baseline_test`, `supported_by`, …).
- **Safety as edges.** `requires_emergency_action`, `must_not_self_adjust`,
  `requires_clinician_decision`, `does_not_replace`, `not_interchangeable_with`,
  `requires_context_lock` are relationships the generator must obey, so they
  survive single-card retrieval and can be checked mechanically.
- **Evidence.** Every node and edge records source id, grade (label, guideline,
  trial, internal), locator, minimal quote and SHA-256 of the excerpt. Nothing is
  marked verified without it.
- **Retrieval.** BM25 over entity text, aliases, neighbour edges and evidence
  titles, with name-match and safety-node boosts and a character-budgeted top-k.
- **Evaluation.** 246 questions with hidden per-question rubrics. Six dimensions
  scored 0 / 0.5 / 1 (coverage, accuracy, completeness, safety, traceability,
  consistency) and five red lines that veto a pass. The answering arm sees the
  rubric; a separate judge model scores. Run manifests carry SHA-256 digests of
  KB, benchmark, prompts and config.
- **Feedback.** `analyze_eval.py` turns scored exports into a defect profile
  (which questions fail, on which dimension, on every model);
  `validate_kb.py --defects` checks the next KB batch closes those gaps.

## Quick start

```bash
pip install python-docx pdfplumber
python3 kb_builder/scripts/validate_kb.py kb_builder/examples/example-kb.json
python3 -m pytest kb_builder/tests                      # no external data needed

pip install -r kb_ab_eval/requirements.txt              # needs KB dir, benchmark, API keys
python3 -m kb_ab_eval.run answers --run-id demo --kb <kb-dir> --benchmark <benchmark.xlsx>
python3 -m kb_ab_eval.run judge   --run-id demo --kb <kb-dir> --benchmark <benchmark.xlsx>
python3 -m kb_ab_eval.run report  --run-id demo
```

Platforms are configured in `kb_ab_eval/config/platforms.example.json` and
`model_eval/models.py`; API keys come from environment variables
(`model_eval/.env.example`). Four tests in `kb_ab_eval/tests` need the KB and
benchmark files locally.

**Stack:** Python 3.12, OpenAI-compatible clients, httpx, Playwright, openpyxl,
python-docx, pdfplumber, Tesseract / macOS Vision OCR, Claude Code skill format.

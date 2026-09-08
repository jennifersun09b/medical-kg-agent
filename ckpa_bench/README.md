# CKPA-Bench

Clinical Knowledge-Prior Arbitration Benchmark for Medical LLMs.

## Setup

```bash
pip install openai PyPDF2 beautifulsoup4
export DEEPSEEK_API_KEY=sk-...

# Optional: pdfplumber, pymupdf for better PDF extraction
pip install pdfplumber pymupdf
```

## Run Full Build

```bash
cd src
python3 run_forge.py --mode mvp --max-drugs 150000
```

Incremental save + crash recovery. Kill & restart safely.

## Evaluate

```bash
python3 src/evaluator.py --mode both
```

## Project Structure

```
src/
  pipeline.py    — 4-layer benchmark constructor
  evaluator.py   — target model evaluator + Arbitration Gain
  run_forge.py   — CLI entry
  prompts.py     — all LLM prompts
  config.py      — API + paths
  api_client.py  — OpenAI-compatible API wrapper
  data_loader.py — CSV/JSONL loading
  content_extractor.py — DXY HTML parser
  pdf_extractor.py     — AMBOSS/DXY PDF extraction + BM25

output/         — generated benchmark items
专业知识/        — raw data (DXY guidelines, drug CSV, AMBOSS PDFs)
docs/           — pipeline documentation
```

## Layers

| Layer | Source A | Source B | Measures |
|-------|----------|----------|----------|
| Core | DXY decision | AMBOSS PDF | jurisdiction-dependent regime selection |
| Temporal | Old DXY guideline PDF | New DXY guideline PDF | version drift |
| Drug-label | Drug label CSV | DXY decision | regulatory-safety override |
| Combination | Drug A label | Drug B label | drug-drug interaction safety |

## Eval Modes

- `no_source`: model sees only patient scenario + question (closed-book)
- `source_provided`: model also sees conflicting source excerpts (open-book)
- `Arbitration Gain` = source_provided accuracy − no_source accuracy

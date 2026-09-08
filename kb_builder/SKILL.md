---
name: build-agent-kb
description: Use when building or extending a structured, source-traceable knowledge-base in Markdown or Word from PDFs, DOCX, Excel, scanned docs, labels, guidelines, research, or project templates for human review, RAG preparation, or long-context use across domains.
---

# build-agent-kb

## Overview
A **reusable engine** that turns raw source material into a **structured knowledge base** whose skeleton is knowledge-graph discipline (entities, types, normalization, explicit relationships) but whose container is readable Markdown or Word. The output is the review artifact; the structured JSON remains available for RAG preparation or compact long-context export.

Core principle: **entities and relationships must be explicit, separated, and source-traceable — never buried in prose.** A knowledge base is not an essay. **The user reviews at two checkpoints; the agent does the reading, searching, verifying, extraction, and generation.**

**Source of truth = the KB JSON.** Edit the JSON and regenerate the output — never hand-edit the .md or .docx (it desyncs from the JSON).

**Keep the skill generic.** Disease-, drug-, product-, competitor-, database-, and project-specific knowledge belongs in the project's sources and JSON, not in this skill. The skill supplies reusable structures only.

**Build against measured defects, not assumed ones.** Authoritative sources decide what is *true*; a model-evaluation export decides what is *missing*. When the project has one, the KB is prioritized by where models measurably fail — not by where a source happens to be thin. See `references/defect-driven-optimization.md`.

## When to Use / Not
- **Use when:** building or extending a domain KB deliverable in Markdown or Word (retains retrievable structure) from labels, guidelines, PDFs, prior docx; switching the same method to a new disease/drug.
- **Not for:** a graph-database build (use graph tooling), a one-off summary, or free-form prose.

## Dependencies
**Core (required):**
- `pip3 install python-docx pdfplumber` (docx generation; PDF reading/number-checking)

**Extended source support (optional):**
- `pip3 install pandas openpyxl` (Excel/CSV extraction)
- `pip3 install pytesseract pdf2image` + `brew install tesseract` (OCR for scanned PDFs)
- macOS only: `pip3 install pyobjc-framework-Vision` (native Vision OCR)

Scripts live at `~/.claude/skills/build-agent-kb/scripts/` and run from any folder via absolute path.

## Workflow (materials list → collect → build; 2 review checkpoints + a collection gate)

```
1 Scope+Template ─▶ 2 Read existing + coverage ─▶ 3 Defect profiling (if eval data exists)
 ─▶ 4 Draft 《所需资料清单》(risk-weighted)
 ─▶ [CP① user reviews the materials list]
 ─▶ 5 Collect: auto-download + verify; REPORT human-collect items ─▶ [GATE: collection complete]
 ─▶ 6 Extract·normalize·relate ─▶ 7 Author JSON ─▶ 8 Generate Word
 ─▶ [CP② user reviews + medical-review checklist]
 ─▶ 9 Verify numbers vs originals ─▶ 10 Housekeeping ─▶ loop next batch
```
**Do not start building (step 6) until sources are collected and the user confirms the list is complete.**

**1 — Scope + Template.** Confirm deliverable is structured **Word** (not a graph). Identify the project's **template** (a spec/建设结构 doc that lists the top-level themes and, per theme, the "content directions" / example questions — reuse it as the KB's backbone; otherwise propose a theme framework). Set audience and **compliance profile** (`references/source-and-compliance.md`; pharma default).

**2 — Read existing + coverage.** Extract every source already on hand (`scripts/extract_docx.py`; `scripts/extract_pdf.py`). Build a **coverage matrix**: theme × (🟢 usable / 🟡 partial / 🔴 gap). Identify which authoritative layer is missing per gap.

**3 — Defect profiling (only if the project has model-evaluation data; skip otherwise).** A coverage matrix answers "what material exists". It does not answer "where do models actually get this wrong" — and those are different questions. If the project has an evaluation export (questions × models × scored dimensions), profile it:

```bash
python3 ~/.claude/skills/build-agent-kb/scripts/analyze_eval.py eval-export.json -o defect-profile.json
```

This emits `defect-profile.json` plus a readable `缺陷图谱.md`. Then **read the samples and do the semantic work the script deliberately does not**: the script counts, ranks, and extracts verbatim examples; you cluster them into concrete knowledge gaps. Pay particular attention to `question_hotspots` where `universalDimensions` is non-empty — a dimension every scored model failed *on the same question* is a hole in the public corpus, and it names both the question and the kind of knowledge missing. See `references/defect-driven-optimization.md`.

**4 — Draft the required-materials list (《所需资料清单》).** From (template themes × coverage gaps × source hierarchy × **defect profile**), list the specific authoritative sources this KB needs — each classified:
- **[本地已有]** — already provided;
- **[可自动获取]** — downloadable (guideline PDF, label, gov/company doc) — give the URL;
- **[需人工收集]** — paywalled/login-only (知网、CSCO 指南 App、期刊官网) — give the **exact citation + PMID/DOI + where to get it**.

**Order by risk, not by theme order.** When a defect profile exists, sources that close a P0 defect or a universal-failure hotspot come first; a 🔴 gap nobody's models get wrong ranks below a 🟡 topic every model fabricates.

**➤ CP① STOP** — present this materials list for review. The user confirms scope / adds / removes sources **before any collection**. (This replaces a vague "gap map" — the user reviews a concrete shopping list.)

**5 — Collect sources.** Auto-download the retrievable items into a `source/` archive (verify each is the real file, not an error/login page — check type & size). **Verify citations exist** (PubMed E-utilities, `references/source-and-compliance.md`) and record file → journal → PMID/DOI in `source/来源清单.md`. **Report the [需人工收集] items** clearly (what, exact citation, PMID, where to get) so the user can fetch them.

**Handling ambiguous or non-standard sources:**
- **微信/钉钉截图** → OCR extract (`scripts/extract_image_pdf.py` or `smart-ocr` skill) + tag as `[内部讨论·待核实·YYYY-MM-DD]`. Place in a separate `临时证据` section in the JSON. At CP② remind the user these need replacement with formal sources.
- **Excel 汇总表/数据表** → Use `scripts/extract_structured.py` to parse. Trace each row back to its original source when possible. If untraceable, tag as `[二手数据]` and downgrade to `[I]` grade.
- **"XX 说的" / "会上提到" (oral information)** → Record as `[口头信息·YYYY-MM-DD·发言人姓名]`. Never use for key medical conclusions; acceptable only for internal context or process notes.
- **404/失效 URL** → Try Wayback Machine (`https://web.archive.org/`). If found, tag as `[原链接失效·存档版本·YYYY-MM-DD]`. If not found, mark as `[链接失效·待替换]` and flag for user follow-up.
- **扫描件 PDF (image-based)** → Detected by `extract_pdf.py` returning < 100 chars for file > 500KB. Auto-invoke OCR via `extract_image_pdf.py`. On OCR failure, tag as `[需人工录入]` and report to user.
- **知网/万方导出 (Refworks/Endnote format)** → Parse via `extract_structured.py`. Extract title, authors, journal, year, abstract. Cross-check against the source hierarchy — use only if no higher-grade source is available.

**➤ GATE — do not proceed to building until the user confirms collection is complete** (has supplied the human-collect items, or explicitly waived them).

**6 — Extract · classify · normalize · relate:**
- **列全实体** from themes, template content-directions, and sources.
- **分类** by type (`references/entry-schema.md`).
- **归一化** synonyms to ONE standard name in a normalization table; keep 同义(=) / 上下位(is-a) / 相关(related) distinct — never over-merge (e.g. ONJ ≠ MRONJ/DRONJ). Where two entities are *routinely* confused, record the pair in `appendix.易混淆对` — the explicit do-not-merge assertion — and give each entity an `易混淆辨析` field so the distinction survives single-card retrieval.
- **关系 = separate edges** with standard verbs (作用于/抑制、增加风险、需监测…), never vague "相关". **Mechanism ≠ relationship:** an entity's mechanism field holds only its OWN mechanism (disease→pathophysiology, target→pathway, AE→how-it-arises); a drug's action ON that entity is a separate relationship edge, and the drug's pharmacology is written **once** (on the drug + target entries), not repeated in every disease card.
- **Defect remediation** (when step 3 ran): for each P0 defect, add the entity, field, or anti-pattern card that closes it. The four remediation fields (`易混淆辨析`, `边界与禁止表述`, `风险信号与行动`, `可执行下一步`) each answer one recurring failure mode — see `references/defect-driven-optimization.md`.

**7 — Author KB JSON** (`references/entry-schema.md`): a concise title followed by the knowledge content itself: optional summary table, fielded entries, explicit relationships, project-defined topic sections, QA, and sources. Use structured `evidence` when a claim or answer needs direct traceability — **every hard number is a claim that needs it**; fabricated figures are the most common measured failure. Add `antipatterns` cards for defects that need the wrong answer shown alongside the right one. Stable IDs are optional, not required by this skill. Keep coverage checks as working notes; only include them in the Word when the user explicitly asks for an audit view.

**8 — Generate deliverable (Markdown or Word):**

**Markdown (default, recommended):**
```bash
python3 ~/.claude/skills/build-agent-kb/scripts/build_kb_md.py your-kb.json out.md
```
Generates clean, readable Markdown with tables, structured fields, and evidence blocks. Markdown is version-control friendly, easier to review/diff, and works across all platforms.

**Word (for formal delivery requiring .docx):**
```bash
python3 ~/.claude/skills/build-agent-kb/scripts/build_kb_docx.py your-kb.json out.docx
```
Generates styled Word document with the same content structure.

Both formats have no cover page, version table, method note, coverage table, header, or footer by default. They begin with one title and then the knowledge content. Optional front matter and internal audit tables require explicit JSON switches; do not enable them unless requested.

**➤ CP② STOP** — deliver the output (Markdown or Word) for review (field granularity, numbers, compliance wording). Optionally generate a one-page **medical-review checklist** listing what the domain expert must confirm.

**9 — Verify numbers vs originals.** Extract the downloaded PDFs (`extract_pdf.py`) and cross-check every incidence / price / dose / HR against the original label/guideline; correct any drift (cite the verified figure). Run automated quality checks:
```bash
python3 ~/.claude/skills/build-agent-kb/scripts/validate_kb.py your-kb.json [--defects defect-profile.json]
```
The validator generates a `质量报告.md` highlighting orphan entities, missing sources, normalization conflicts, inconsistent relationship verbs, empty required fields, incomplete anti-pattern cards, do-not-merge pairs that contradict the normalization table, and numeric claims with no traceable evidence. Pass `--defects` to add a coverage table checking that every P0 defect from step 3 has a corresponding remedy in the KB. Fix errors (exit code 2) before delivery; address warnings (exit code 1) as time permits.

**10 — Housekeeping.** Name outputs with a `_YYYYMMDD` date suffix; keep `source/` + manifest; maintain a project `memory.md` (status, decisions, deliverables, next steps). Delete superseded intermediates. (These conventions also live in global `~/.claude/CLAUDE.md`.)

## Non-negotiable disciplines
- **Source-traceable:** every fact carries a source + grade ([S] label / [G] guideline / [T] trial / [I] internal); citations verified to exist (PMID/DOI).
- **Claim-level evidence:** when structured `evidence` is used, every item must contain a claim and source; original location and scope are optional but recommended.
- **Don't exceed the source:** no claim beyond approved label/evidence; no un-approved indication, no exaggeration; foreign-label ≠ domestic — mark it.
- **Mechanism ≠ relationship;** drug pharmacology once, not per card.
- **Normalize, don't flatten;** fill every relationship with a standard verb. Two entities that are routinely confused belong in `appendix.易混淆对`, never merged as `同义(=)`.
- **Measured defects outrank assumed gaps:** when evaluation data exists, it — not intuition — decides what the KB prioritizes.
- **QA is template-driven:** cover the template's validation questions + every content-direction; drop impractical ones.
- **Competitor fairness (pharma):** no unsubstantiated superiority; biosimilars are "highly similar", not "worse".
- **Project content stays outside the skill:** add project-specific treatment landscapes, comparisons, combinations, and databases through project JSON and sources.
- **Reader-facing structure stays minimal:** title first, then knowledge content. Internal governance and audit structures stay outside the default Word.

## Red flags — STOP and fix
- Prose paragraphs instead of fielded entries → restructure into cards.
- Drug pharmacology re-narrated inside a disease card → move to a relationship edge.
- A relationship as "相关/有关", or an empty `(  )` → use a standard verb.
- A number/citation with no verified source → verify or remove; never invent a PMID/journal.
- A structured evidence item with no claim or source → fix the JSON; the generator rejects it.
- Project-specific facts hard-coded into the skill → move them to the project JSON or source archive.
- Front matter, version notes, coverage matrices, or keyword audits appearing without an explicit request → keep them in working files, not the default Word.
- Collecting sources before CP① → stop, present the 《所需资料清单》 first.
- Building (entities/JSON) before the collection GATE → stop; sources must be collected and confirmed complete first.
- Evaluation data available but step 3 skipped, going straight to the materials list → stop; the list would be ordered by assumption instead of by measurement.
- A P0 defect from the profile with no matching entity, field, or anti-pattern card → stop; the KB does not yet do the job it was built for.
- Two entities listed in `易混淆对` **and** merged as `同义(=)` in the normalization table → stop; one of the two assertions is wrong.
- An anti-pattern card without `正确说法` or `来源` → stop; "don't say X" with no replacement leaves the gap that produced X.
- Hand-editing the .docx → edit the JSON and regenerate instead.

## Files
- `scripts/build_kb_md.py` — data-driven JSON→Markdown generator (default, recommended for version control and review).
- `scripts/build_kb_docx.py` — data-driven JSON→Word generator (for formal delivery requiring .docx format).
- `scripts/validate_kb.py` — automated quality checks (orphan entities, missing sources, normalization conflicts, relationship verb consistency, anti-pattern completeness, do-not-merge contradictions, unsourced numbers, and — with `--defects` — measured defect coverage).
- `scripts/analyze_eval.py` — aggregate a model-evaluation export into a defect profile (`defect-profile.json` + `缺陷图谱.md`); built-in profile for the GEO export schema, `--field-map` for others.
- `scripts/extract_docx.py` / `scripts/extract_pdf.py` — dump source .docx/.pdf text (+ number-check helper).
- `scripts/extract_image_pdf.py` — OCR extraction for scanned/image-based PDFs (tesseract or macOS Vision).
- `scripts/extract_structured.py` — parse Excel/CSV, 知网 Refworks exports, ClinicalTrials.gov JSON into normalized format.
- `tests/test_build_kb_md.py` / `tests/test_build_kb_docx.py` — regression tests for minimal output, headings, structured evidence, generic topic tables, audience answers, anti-pattern cards, do-not-merge tables, and validation.
- `tests/test_analyze_eval.py` / `tests/test_validate_kb.py` — regression tests for defect aggregation and the quality checks.
- `references/entry-schema.md` — full KB JSON schema, entity types, relationship verbs, normalization kinds, do-not-merge pairs, anti-pattern cards.
- `references/defect-driven-optimization.md` — how measured model defects map onto KB primitives; the `universal` criterion; evaluation-export field contract.
- `references/source-and-compliance.md` — source grading, **multi-channel citation verification** (PubMed, DOI, 中文期刊, 专利, 法规, 内部材料), compliance profiles.
- `examples/example-kb.json` — neutral worked example exercising the reader-facing JSON features.
- `examples/example-defect-profile.json` — small neutral defect profile for trying `validate_kb.py --defects`.

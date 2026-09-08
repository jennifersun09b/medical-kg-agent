# Entry Schema — KB JSON reference

The generator `scripts/build_kb_docx.py` consumes ONE JSON file. This is the contract.
See `examples/example-kb.json` for a filled instance. Empty/missing fields are skipped.

## Top-level object

| Key | Meaning |
|---|---|
| `title` (required) | Document title; the first paragraph |
| `show_front_matter` | Optional boolean, default `false`; only `true` renders `subtitle` / `meta` / `note` |
| `subtitle` / `meta` / `note` | Optional front matter; ignored by default |
| `output` | Default output filename if none passed on CLI |
| `cn_font` | eastAsia font, default `微软雅黑` |
| `show_audit_sections` | Optional boolean, default `false`; only `true` renders internal coverage/keyword audit tables |
| `coverage_map` / `keyword_map` | Optional internal audit data; omitted from the default Word |
| `summary_columns` / `summary_table` | Column order / list of row objects (the 术语与实体总表 index of ALL entities) |
| `sections` | List of `{title, entries[]}` — the detailed entry cards, grouped by theme |
| `topic_sections` | Optional reusable tables for project-defined comparisons, pathways, combinations, or other topics |
| `antipatterns` | Optional anti-pattern cards: recorded wrong answers + why + the correct phrasing (see below) |
| `qa_intro` | Small note rendered under the Q&A header (explain the QA structure) |
| `qa` | List of `{q, a, entities, group?}` or the extended QA form below |
| `appendix` | `{实体类型[], 关系动词[], 关系模板[], 归一化规则[], 易混淆对[]}` |
| `sources` | List of source/compliance lines (lines containing 合规/禁区 render red) |

Default output is deliberately minimal: one title, followed immediately by the knowledge content. It has no cover page, version table, method note, audit table, header, or footer. Keep governance records and working matrices outside the reader-facing Word unless the user explicitly requests them.

## Entry object (each item in `sections[].entries`)

`标准名` required; other fields optional, rendered in this fixed order (extra custom fields last):

1. `标准名` — the ONE canonical name (heading)
2. `实体类型` — see entity types
3. `别名/同义词` — synonyms, brand names, exclusion/易混淆 notes
4. `易混淆辨析` — **do-not-merge** note: the entity this one is most often confused with, and the single discriminator that separates them. The counterpart to field 3: aliases say "these are the same", this says "these are NOT"
5. `一句话定义` — one sentence, does not exceed the source (standard KB gloss — keep it)
6. **mechanism field — use the ONE that fits the entity's own mechanism:**
   - `作用机制/关键内容` — for drugs/targets/cells (its own pharmacology/pathway)
   - `病理机制` — for diseases/pathologies (pathophysiology; NO drug-action narration)
   - `发生机制` — for adverse reactions (how the AE arises)
7. `关键数据/证据` — trial data / incidence / evidence, kept **separate** from mechanism
8. `边界与禁止表述` — what this entity's evidence does **not** support. States the ceiling of the claim ("supported for X; NOT supported for Y")
9. `适应症/适用人群` · 10. `用法用量要点` · 11. `关键安全性` — if applicable
12. `风险信号与行动` — trigger → action pairs: the observable signal and what to do about it, in that order. Not a list of risks; a list of responses
13. `关联实体与关系` — other entities + **standard verb** in parentheses; the drug↔entity action lives HERE as an edge, not inside the mechanism field
14. `可执行下一步` — the concrete next action a reader can take. "遵医嘱" is not an answer; name the test, the timing, the threshold, or the person to ask
15. `常见问题` — anticipated Q + answer
16. `来源` — source(s) + grade tag + verified citation (journal / PMID / DOI)
17. `合规备注` — boundaries; embed `【…·合规禁区】` for forbidden phrasings
18. `evidence` — optional claim-level evidence list; rendered as a compact evidence table

**Mechanism ≠ relationship:** the mechanism field is the entity's OWN mechanism; a drug's action on it is a relationship edge (field 13). Drug pharmacology is written once (drug + target cards), not repeated per disease.

### The four defect-remediation fields

Fields 4, 8, 12, and 14 exist because measured model evaluations keep failing in the same four ways. Each is optional — omit it when the entity has no such exposure — but when an entity *is* a known failure site, the matching field is where the fix goes. `references/defect-driven-optimization.md` has the full derivation.

| Field | Answers the question | Fixes |
|---|---|---|
| `易混淆辨析` | "What gets mistaken for this, and how do I tell them apart?" | Two entities merged into one; specs of A attributed to B |
| `边界与禁止表述` | "What does the evidence *not* let me say?" | Overclaiming past what the source supports |
| `风险信号与行动` | "What do I watch for, and what do I do?" | Danger signs mentioned but not acted on, or omitted |
| `可执行下一步` | "What do I actually do next?" | Answers that end in a non-answer like "consult a professional" |

Write them as content, not as warnings *about* content: `边界与禁止表述` states the evidence ceiling, and `合规备注` carries the `【…·合规禁区】` phrasing bans. Keep both — one is epistemic, the other regulatory.

### Claim-level evidence — `evidence`

Use a list of objects when a claim must be directly traceable:

```json
"evidence": [
  {
    "claim": "The exact supported claim.",
    "source": "[G] Exact guideline or paper citation",
    "location": "Page, section, table, or paragraph",
    "scope": "Population, setting, time, or other boundary"
  }
]
```

`claim` and `source` are required. `location` and `scope` are optional but recommended. Keep the existing `关键数据/证据` and `来源` text fields for short human-readable summaries; use `evidence` for direct claim-to-source mapping. No ID fields are required.

## Entity types

Entity types are project-defined. Keep the set small, stable, and reusable within one KB. Generic examples include `概念`, `主体`, `对象`, `过程`, `事件`, `规则`, and `指标`. A project may define more specific types in its own JSON or project instructions; those types do not belong in this reusable skill.

## Relationship verbs

Define a concise verb set for each project and use it consistently. Prefer explicit relations such as `属于`, `组成`, `作用于`, `引起`, `增加风险`, `降低风险`, `需监测`, or `适用于`. Avoid bare `相关/有关` when a more precise relation is supported. Never leave an empty relationship marker.

## Normalization rules (归一化) — `appendix.归一化规则`
List of `{出现的写法, 归一到, 关系类型}`. Record the relation **kind**, and be exhaustive (cover every alias/variant in the KB):
- **同义(=)** — two forms with the same meaning
- **命名对应** — a display name mapped to its canonical name
- **上下位(is-a)** — a narrower term mapped to its broader class
- **相关(related)** / **语境归类** — associated or context-dependent forms

Over-merging is a defect: if two forms are not exactly synonymous, use is-a / related, not `=`.

## Do-not-merge pairs (易混淆对) — `appendix.易混淆对`

The **inverse assertion** of a `同义(=)` rule. `归一化规则` says "these forms mean the same thing, collapse them"; this says "these look alike and must never be collapsed." It is a separate table precisely because it is a negative claim — burying it inside `归一化规则` would put an assertion and its negation in one list.

```json
"易混淆对": [
  {
    "A": "Canonical name of the first entity",
    "B": "Canonical name of the second",
    "区别维度": "The ONE dimension that separates them (dose, population, route, scope…)",
    "混淆后果": "What actually goes wrong when they are merged",
    "来源": "[S] Source establishing the distinction"
  }
]
```

A pair listed here must not also appear in `归一化规则` as `同义(=)` — that is a direct contradiction, and `validate_kb.py` treats it as an error. Both `A` and `B` should carry an `易混淆辨析` field on their own entry cards, so the distinction survives when a card is retrieved on its own.

## Anti-pattern cards — `antipatterns`

A recorded instance of a real wrong answer, paired with the correct one. Ordinary KB fields say what is true; an anti-pattern card says what was actually said wrongly and why — which is what stops the same error being reproduced.

```json
"antipatterns": [
  {
    "id": "AP-01",
    "缺陷代码": "R4",
    "缺陷名称": "Short label for the failure mode",
    "命中模型": ["model-a", "model-b"],
    "命中次数": 87,
    "universal": true,
    "错误表述": "The wrong statement, quoted from the evaluation (truncated)",
    "为什么错": "The mechanism- or evidence-level reason it is wrong",
    "正确说法": "A compliant, supported replacement statement",
    "关联实体": ["Entity name"],
    "来源": "[S] Source backing the correct version",
    "evidence": [{"claim": "...", "source": "..."}]
  }
]
```

Required: `缺陷代码`, `错误表述`, `为什么错`, `正确说法`, `来源`. The generators reject a card missing any of them.

Three rules make these cards worth writing:

1. **Quote, don't paraphrase.** `错误表述` is the model's actual output. A cleaned-up paraphrase loses the specific phrasing that has to be recognized.
2. **Always supply `正确说法`.** A card that only says "don't say X" leaves the reader with nothing to say instead, and the gap gets filled by the same wrong answer.
3. **The correct version carries a source.** Otherwise the card replaces one unsourced claim with another.

`命中模型` / `命中次数` / `universal` are optional provenance from `analyze_eval.py`; they let a reviewer see whether a card addresses a systemic gap or one model's quirk. Anti-pattern cards render whenever present — no switch, same as `topic_sections`.

## Relation templates (关系模板) — `appendix.关系模板`
List of `{主体类型, 关系, 客体类型, 示例}`. Encodes the type-pair rules that let you batch-build
relationships (e.g. `主体 —执行→ 过程`, `事件 —触发→ 规则`, `指标 —用于评估→ 对象`).

## QA — `qa` + `qa_intro`
The compact form remains `{q, a, entities, group?}`. The optional extended form is:

```json
{
  "group": "Theme",
  "q": "Question",
  "answers": [
    {"audience": "Professional", "text": "Answer for this audience"},
    {"audience": "Public", "text": "Answer for this audience"}
  ],
  "boundary": "Safety, compliance, or applicability boundary",
  "entities": "Traversed entity chain",
  "evidence": [{"claim": "Supported answer claim", "source": "[G] Source"}]
}
```

`group` renders a sub-header when it changes → group the QA by theme.
Structure: pin the **template's validation questions** as anchors first (their own group), then one practical
Q per template content-direction, grouped by theme. Every answer is multi-hop; `entities` lists the traversed chain.
Drop impractical questions.

## Generic topic tables — `topic_sections`

Use this optional module for any repeated project-defined comparison or pathway. The skill does not prescribe the topic or column names.

```json
"topic_sections": [
  {
    "title": "Project-defined topic",
    "intro": "Optional reading note",
    "columns": ["Option", "Role", "Evidence"],
    "rows": [
      {"Option": "A", "Role": "Project-provided description", "Evidence": "[G] Source"}
    ]
  }
]
```

Projects may use this for comparisons, treatment or decision pathways, combinations, or other domains. All facts and categories come from the project JSON and sources, never from the skill itself.

# Source Grading & Compliance Profiles

Every fact in a KB entry carries a **source tag + grade**. This is what makes the
Word deliverable trustworthy as an agent/RAG brain: a claim you can't trace, you delete.

## Source grade tags
| Tag | Layer | Examples |
|---|---|---|
| `[S]` | **Primary label / spec** — highest authority | NMPA 说明书、FDA DailyMed、EMA EPAR |
| `[G]` | **Guideline / consensus** | CSCO、中国抗癌协会、专科专家共识 |
| `[T]` | **Pivotal trial / literature** | 注册 III 期研究、PubMed 主文献 |
| `[I]` | **Internal approved material** | 医保申报材料、企业公告、已批准医学/推广材料 |

**Precedence when sources conflict:** `[S] > [G] > [T] > [I]`. The label wins.
For anything region-specific (indications, dosing, pricing), the **current domestic label** is source-of-truth; foreign labels are background only.

## The source hierarchy drives gap-filling
When a theme is a 🔴 gap, fill it from the **highest available layer first**:
1. domestic label → 2. national guideline/consensus → 3. pivotal trial → 4. internal.
Record the grade with each fact. Never fabricate numbers, prices, approval dates, or
head-to-head claims. If unverified: write `未找到可核验公开值` — do not estimate.

## Citation verification (do this BEFORE citing a guideline/paper)
LLMs hallucinate references and mis-attribute journals. Verify existence, don't trust memory. Use the highest-confidence method available; fall back gracefully when verification is impossible.

### Multi-channel verification flow (按优先级)

**1. PMID → PubMed E-utilities** (no key needed, highest confidence for MEDLINE-indexed journals):
```bash
EU="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&retmode=json&term="
# by journal + year + first page (most precise for a known consensus)
curl -s "${EU}Zhonghua%20Zhong%20Liu%20Za%20Zhi%5Bta%5D+AND+2024%5Bdp%5D+AND+637%5Bpg%5D"   # → idlist = PMID
# then confirm title/journal/pages match what you intend to cite:
curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id=<PMID>"
```
Read the returned `source` (journal), `pubdate`, `volume:pages`, `title` and confirm they match.

**2. DOI → CrossRef API / DataCite** (for non-MEDLINE journals, preprints, datasets):
```bash
# CrossRef (works for most journals with DOIs)
curl -s "https://api.crossref.org/works/10.1234/example.doi" | jq '.message | {title, author, published, container_title}'
# DataCite (for datasets, repositories, grey literature)
curl -s "https://api.datacite.org/dois/10.1234/example.doi" | jq '.data.attributes | {titles, creators, publicationYear, publisher}'
```
Confirm title, authors, year, publisher match. Record the verified DOI.

**3. 中文期刊 → 万方/知网 title+author+year 查询** (requires user-configured API key or manual lookup):
- **万方数据 API**: if user has provided API credentials in project config
- **知网 (CNKI)**: if user has provided account or direct article link
- **Manual fallback**: cite as `[G] 作者. 标题. 期刊名, 年;卷(期):页码 [中文期刊·未收录 MEDLINE]`
- A Chinese-journal consensus absent from PubMed is acceptable — cite by full bibliographic details, don't invent a PMID.

**4. 专利 → 专利号格式验证 + 可选 Espacenet 查询**:
- Validate patent number format: `CN\d{9}[A-Z]` (China), `US\d{7,8}[A-Z]\d?` (USA), `EP\d{7}[A-Z]\d` (Europe), `WO\d{4}/\d{6}` (PCT)
- Optional verification via Espacenet: `https://worldwide.espacenet.com/patent/search?q=pn%3D{patent_number}`
- Cite as `[专利] 专利号 · 专利名称 · 申请人 · 公开日期`

**5. 法规文件 → URL + 发布机构 + 文号 三要素核对**:
- Verify URL is from an authoritative domain (e.g., `nmpa.gov.cn`, `fda.gov`, `ema.europa.eu`)
- Extract and record: 发布机构 (issuing body), 文号 (document number), 发布日期 (publication date), 标题 (title)
- Cite as `[法规] 发布机构. 文号 · 标题 (YYYY-MM-DD). URL`
- If URL becomes 404, attempt Wayback Machine recovery and tag `[原链接失效·存档版本]`

**6. 内部材料 → 文件指纹 (SHA256) + 审批记录引用**:
- Calculate file hash: `shasum -a 256 filename.pdf`
- Record: 文件名, 审批部门/编号 (if available), 日期, SHA256 hash
- Cite as `[I] 文件名 · 审批记录/内部编号 · 日期 [SHA256: first_8_chars]`
- Keep original file in `source/internal/` archive

**7. Fallback: 记录但标记未核验**:
When none of the above methods apply or succeed (paywall blocks access, source is oral/informal, database is unavailable):
- Tag as `[未核验·仅记录]` in the 来源 field
- Record all available bibliographic details: title, author(s), source, date, URL (if any), access method
- Add to `source/未核验清单.md` for manual follow-up
- **Never invent a PMID, DOI, or journal name.** Partial information honestly tagged is better than fabricated completeness.

### Number verification

**Verify numbers against the original** once the PDF is downloaded (`scripts/extract_pdf.py`):
extract the label/guideline text and grep the exact incidence / price / dose / HR; correct any drift to the
verified figure. Prices/AE rates in a secondary "汇编" must trace back to the primary label.

### Manifest recording

**Record** in the `source/来源清单.md` manifest: file → journal/source → PMID/DOI/专利号/文号 → verification method → which KB theme it supports.
If you discover a mis-attribution (wrong journal, a "consensus" that is actually an old book), fix it in the KB and log it in the manifest.

## Claim-level evidence

For conclusions that need direct traceability, use the JSON `evidence` list with:

- `claim` — the supported statement;
- `source` — exact source with grade;
- `location` — page, section, table, or paragraph when available;
- `scope` — population, setting, time, and other applicability limits.

The generator requires `claim` and `source`. Apply the same structure to QA when an answer contains medical, legal, financial, or other consequential claims.

External research databases and screening portals are discovery sources unless their records point to an authoritative original study. Do not convert an experimental, computational, observational, or database association into a clinical or operational recommendation without confirming it in the appropriate label, guideline, trial, regulation, or primary source.

## Compliance profile — PHARMA (default)
Bake these boundaries into 合规备注. Phrase forbidden claims as `【…·合规禁区】`.

| Don't say | Why | Say instead |
|---|---|---|
| "X 比所有生物类似药疗效更好/更安全" | Biosimilars are approved on *similarity*, not proven inferior | "X 具备成熟原研证据与长期使用经验；生物类似药以各自说明书/公开研究为准" |
| "肾功能不好完全不用担心" | No dose adjustment ≠ no monitoring; hypocalcemia risk rises | "无需按肾功能调剂量，但需做好钙/镁/磷监测与补钙维D" |
| "没有 ONJ / 低钙风险" | Label explicitly warns these | "有不同安全性管理重点，需提前口腔与电解质管理" |
| "可按外国标签用于〔本国未批适应症〕" | Foreign indication ≠ domestic approval | "以本国获批说明书为准；海外标签仅作内部背景" |
| "〔老药/对照药〕已过时/无效" | Still approved, evidenced, low-cost | "是成熟低成本选择；差异在机制/给药/管理负担" |

**Universal rules (all profiles):**
- No claim beyond the approved label / cited evidence.
- Distinguish domestic vs. foreign, generic vs. brand vs. dose (e.g. 120mg vs 60mg products).
- Numbers, prices, approval status: sourced or omitted, never invented.
- AI output is a **draft**; must pass medical + compliance review before external use.

## Adapting to a non-pharma domain
Swap the pharma table for your domain's boundaries (e.g. finance: no forward-looking
guarantees; legal: no jurisdiction-crossing advice). Keep the universal rules and the
`[S]/[G]/[T]/[I]` grading — they are domain-agnostic. State the active profile at Scope (step 1).

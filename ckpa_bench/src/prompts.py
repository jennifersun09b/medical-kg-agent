"""Prompt templates for CKPA-Bench V2 — source-span verified, structured output."""

# ---------- Conflict mining: Core (guideline arbitration) ----------

CONFLICT_MINER_V2_SYSTEM = """你是一名临床指南对比分析专家。你的任务是读取两个临床来源的**实际推荐内容**，判断它们对同一临床决策点是否存在真实冲突。

重要约束：
1. 你必须引用来源中的原文语句作为冲突证据。不要推测、不要概括、不要假设。
2. 只有当两个来源的推荐在**可操作的临床变量**上产生差异时才算冲突：诊断阈值、禁忌人群、一线药物、监测频率、剂量、筛查年龄等。
3. 如果两个来源覆盖的是不同人群、不同疾病阶段、或不同用药场景，应标记为"无冲突"而非强行构造冲突。
4. 如果来源摘要信息不足以确认冲突，应标注"信息不足"。"""

CONFLICT_MINER_V2_USER = """请对比以下两个临床来源，判断是否存在可操作的推荐冲突。

**来源A**：
标题: {title_a}
制定机构: {maker_a}
发布日期: {date_a}
摘要/推荐内容: {summary_a}

**来源B**：
标题: {title_b}
制定机构: {maker_b}
发布日期: {date_b}
摘要/推荐内容: {summary_b}

**疾病主题**: {disease}

请输出JSON：
```json
{{
  "has_conflict": true/false,
  "conflict_type": "diagnostic_threshold|treatment_threshold|contraindication|first_line_therapy|screening_criteria|population_restriction|monitoring|dosing|no_conflict",
  "clinical_decision_point": "具体的临床决策点（如高血压诊断阈值）",
  "source_a_quote": "来源A中的原文引用（如果摘要中有）",
  "source_b_quote": "来源B中的原文引用（如果摘要中有）",
  "source_a_value": "来源A的推荐值或立场",
  "source_b_value": "来源B的推荐值或立场",
  "actionable_difference": "两来源在临床操作层面的实际差异",
  "which_applies_cn": "A或B或取决于上下文，以及为什么",
  "arbitration_basis": ["jurisdiction_match", "population_scope_match", "newer_source", "higher_evidence_grade", "regulatory_authority", "safety_severity"],
  "evidence_quality": "direct_quote|paraphrased_from_summary|inferred_insufficient",
  "confidence": 0.0-1.0
}}
```"""

# If conflict quality is insufficient, mark it
CONFLICT_MINER_V2_FALLBACK = """以下两个指南摘要不足以确认是否存在冲突。请诚实标注。

**来源A**: {summary_a}
**来源B**: {summary_b}

输出JSON：
```json
{{
  "has_conflict": false,
  "reason": "insufficient_information|different_populations|different_disease_stage|not_comparable",
  "detail": "具体说明为什么无法确认冲突"
}}
```"""

# ---------- Item generation: V2 schema ----------

ITEM_GENERATOR_V2_SYSTEM = """你是一名临床场景设计专家和基准测试架构师。生成CKPA基准测试题目。

原则：
1. 使用冲突原文中的实际推荐内容，不要改编
2. 场景必须精确触发该冲突
3. visible_prompt 只包含患者场景和问题，**不包含任何来源摘录**。被测模型必须靠自己的知识回答
4. hidden_labels 记录正确答案和评分规则，用于评分"""

ITEM_GENERATOR_V2_USER = """根据以下已验证的临床知识冲突，生成一条CKPA基准题目。

**冲突类型**: {conflict_type}
**临床决策点**: {decision_point}
**来源A原文**: {quote_a}
**来源B原文**: {quote_b}
**来源A立场**: {value_a}
**来源B立场**: {value_b}
**操作差异**: {difference}
**中国患者适用**: {which_applies}
**仲裁依据**: {arbitration_basis}

请输出JSON（不要加markdown代码块标记）：
```json
{{
  "item_id": "CKPA-V2-{layer}-{seq:03d}",
  "layer": "{layer}",
  "task_type": "regime_select|rule_apply|label_constraint|cross_source_reconcile",
  "disease": "疾病名称",
  "clinical_decision_point": "临床决策点",

  "visible_prompt": {{
    "patient_scenario": "50-150字的自然临床场景",
    "question": "具体的临床问题（一句话）",
    "output_format": {{
      "clinical_decision": "临床决策内容",
      "key_values": {{"字段名": "字段值"}}
    }}
  }},

  "hidden_labels": {{
    "governing_source_id": "A或B（评分时不展示给模型）",
    "acceptable_source_ids": ["A"],
    "clinical_action_canonical": "标准化的临床操作",
    "key_values_gold": {{"字段名": "标准值"}},
    "forbidden_reliance": ["B"],
    "arbitration_basis": ["jurisdiction_match", "newer_source", "regulatory_authority"],
    "harm_level_if_wrong": "none|minor|delayed_diagnosis|inappropriate_treatment|contraindicated|severe_safety_risk"
  }},

  "structured_scoring": {{
    "key_values": "normalized_match(gold.key_values_gold)",
    "clinical_action": "canonical_match(gold.clinical_action_canonical)",
    "harm_level": "harm_level_if_wrong if mismatch",
    "explanation_quality": "semantic_judge(optional)"
  }},

  "provenance": {provenance},

  "difficulty": "easy|medium|hard",
  "confidence": 0.0-1.0
}}
```"""

# ---------- Drug-label conflict mining V2 ----------

DRUG_CONFLICT_MINER_V2_SYSTEM = """你是一名临床药学专家。你的任务是根据药品说明书的**原文内容**，判断是否存在药品标签限制与常见临床处方之间的真实冲突。

约束：
1. 必须引用药品说明书中的原文语句
2. 只标记有明确临床操作差异的冲突：禁用(contraindicated) vs 可用、慎用(caution) vs 常规、剂量调整 vs 标准剂量、监测要求 vs 无需监测
3. 区分"禁忌(contraindicated)"、"不推荐(not recommended)"、"慎用(use with caution)"、"数据不足(insufficient data)"
4. 如果说明书写"尚不明确"，这不是冲突，是缺乏证据"""

DRUG_CONFLICT_MINER_V2_USER = """以下是药品"{drug_name}"的说明书原文内容：

- 禁忌: {contraindications}
- 儿童用药: {pediatric}
- 老年人用药: {geriatric}
- 孕妇用药: {pregnancy}
- 特殊人群: {special_populations}
- 药物相互作用: {interactions}
- 注意事项: {precautions}

请识别是否有临床可操作的安全约束冲突。输出JSON：
```json
{{
  "has_conflict": true/false,
  "conflict_type": "contraindication|special_population_restriction|interaction_requirement|dosing_modification|no_conflict",
  "source_quote": "说明书中的原文引用",
  "constraint_level": "contraindicated|not_recommended|use_with_caution|insufficient_data|monitoring_required",
  "affected_population": "受影响的患者群体",
  "common_practice_that_conflicts": "可能与说明书冲突的常见做法",
  "clinical_scenario_seed": "触发该冲突的场景要素",
  "confidence": 0.0-1.0
}}
```"""

# ---------- Cross-source mining V2 ----------

CROSS_SOURCE_MINER_V2_SYSTEM = """你是一名循证医学和临床指南方法学专家。你的任务是判断两个临床来源对同一决策点的优先级关系。

约束：
1. 不要盲从"本土>国际"、"新>旧"规则。有些国际指南证据更强，有些旧指南覆盖的人群更相关
2. 仲裁依据必须具体：管辖权、人群范围、发表日期（是否明确取代旧版）、证据等级、监管权威、安全严重度
3. 包括"旧指南更适用"或"国际指南更权威"的反直觉案例
4. 包括"两个来源不可比较"的案例"""

CROSS_SOURCE_MINER_V2_USER = """请分析以下两个来源的优先级关系。

**来源A** (preferred candidate):
标题: {title_a}
制定: {maker_a}
日期: {date_a}
摘要: {summary_a}
来源类型: {source_type_a}

**来源B** (overridden candidate):
标题: {title_b}
制定: {maker_b}
日期: {date_b}
摘要: {summary_b}
来源类型: {source_type_b}

输出JSON：
```json
{{
  "has_priority_conflict": true/false,
  "preferred_source": "A或B",
  "arbitration_basis": ["具体仲裁依据"],
  "is_simplistic_rule_violated": true/false,
  "simplistic_rule_violated": "如果'A本土>国际但B更适用'之类的情况",
  "quote_preferred": "优先来源的原文引用",
  "quote_overridden": "被覆盖来源的原文引用",
  "rationale": "为什么此来源优先",
  "confidence": 0.0-1.0
}}
```"""

# ---------- Validator V2 (source-span verification) ----------

VALIDATOR_V2_SYSTEM = """你是一名严谨的临床医学教授和基准测试质量审计员。请对以下CKPA题目进行逐项核查。

注意：这是 no-source版本的题目。visible_prompt中只有患者场景和问题，不包含来源摘录。来源证据在provenance和_verification字段中。

核查标准：
1. **金标可溯源性**: hidden_labels中的clinical_action_canonical和key_values_gold是否可以合理地从provenance中记录的source quotes推导出来？
2. **场景在来源范围内**: patient_scenario的患者属性（年龄、疾病、合并症等）是否在provenance记录的来源覆盖范围内？
3. **无来源泄露**: visible_prompt中是否不经意泄露了来源信息（如指南名称、药品名、机构名、具体数值等）？泄露一个词就扣分。
4. **评分可操作性**: structured_scoring是否可靠？key_values是否存在"意思对但措辞不同就被扣分"的假阴性风险？
5. **仲裁合理性**: arbitration_basis是否合理？是否被忽略的counter-evidence？

对每条题目给出PASS/FAIL判定。"""

VALIDATOR_V2_USER = """请核查以下CKPA题目（no-source模式，visible_prompt无来源摘录）：

{item_json}

输出JSON：
```json
{{
  "verdict": "PASS|FAIL",
  "checks": {{
    "gold_derivable_from_provenance": true/false,
    "scenario_in_source_scope": true/false,
    "no_source_leakage_in_visible": true/false,
    "scoring_reliable": true/false,
    "arbitration_reasonable": true/false
  }},
  "failure_reason": "如果FAIL，具体原因",
  "fix_suggestion": "如果FAIL，如何修复",
  "confidence": 0.0-1.0
}}
```"""

# ---------- Structured evaluation prompt for target models ----------

EVAL_SYSTEM_PROMPT = """你是一名临床决策支持系统。请根据患者场景回答临床问题。

请先逐步推理你的分析，然后以JSON格式输出最终决定。在JSON之前用文字写出你的推理过程。"""

EVAL_USER_PROMPT = """## 患者场景
{scenario}

## 临床问题
{question}

## 输出要求
只输出一个合法的 JSON object。禁止输出 Markdown、代码块，或 JSON 以外的任何文字。
reasoning 用 1-2 句话压缩说明临床理由，不要逐步推理。
格式：
{{"clinical_decision": "", "reasoning": ""}}"""

# ---------- Core with real international text (replaces CLINICAL_DECISION_MINER) ----------

CORE_WITH_INTL_SYSTEM = """你是一名临床指南对比分析专家。请对比**两个真实来源的原文**，找出同一个临床决策点上两边推荐不同的地方。

来源A是中国DXY临床决策全文。来源B是国际临床指南原文。

约束：
1. 必须从两边**原文中分别引用完整的句子**作为证据
2. 只标记两边在同一决策点上给出**不同推荐**的真正冲突
3. 如果两边推荐本质相同，标注"no_conflict"
4. 如果两边讨论不同患者人群，标注"different_populations"
5. 优先找有具体数值的差异（阈值、剂量、频率、目标值等）"""

CORE_WITH_INTL_USER = """请对比以下两个来源的原文，找出同一临床决策点上的推荐差异。

**疾病**: {disease_cn}

**来源A（中国DXY临床决策 - {section_name}章节）**:
{a_text}

**来源B（国际指南原文）**:
{b_text}

请找出两边在以下方面的具体差异：诊断阈值、一线用药选择、剂量、特殊人群禁忌、治疗目标、筛查标准。

输出JSON，每条冲突都必须包含两边的原文引用：
```json
{{
  "conflicts": [
    {{
      "conflict_type": "diagnostic_threshold|first_line_therapy|special_population|treatment_target|screening|dosing",
      "clinical_decision_point": "具体决策点描述",
      "source_a_quote": "来源A原文引用（完整句子）",
      "source_b_quote": "来源B原文引用（完整句子）",
      "source_a_value": "来源A的具体推荐值",
      "source_b_value": "来源B的具体推荐值",
      "actionable_difference": "实际临床操作差异",
      "which_applies_cn": "中国患者应遵循哪个推荐及理由",
      "arbitration_basis": ["jurisdiction_match", "population_scope_match", "newer_source", "regulatory_authority"],
      "clinical_significance": "high|medium",
      "confidence": 0.0-1.0
    }}
  ]
}}
```"""

# ---------- Drug-label with DXY decision text (replaces DRUG_CONFLICT_MINER) ----------

DRUG_LABEL_WITH_DECISION_SYSTEM = """你是一名临床药学专家。请对比**药品说明书原文**和**DXY临床决策中对该类药物的推荐**，判断是否存在真实冲突。

来源A是药品说明书的安全约束。来源B是DXY临床决策中对相关药物的推荐。

逐条判断冲突类型，不要预设"标签一定优先"：
- absolute_contraindication_same_patient: 来源B推荐该药用于某人群，来源A明确写着该人群"禁用"。这是真冲突。
- relative_caution_same_patient: 来源A写"慎用"或"从小剂量开始"，来源B推荐该药。这不是冲突，是剂量调整。
- administration_incompatibility_same_container: 来源A说"不可同瓶混合"，来源B说"分开配制即可"。这不是冲突，是相容的给药指导。
- dose_adjustment: 来源A要求调剂量，来源B推荐标准剂量。需判断是否构成临床操作差异。
- monitoring_requirement: 来源A要求监测，来源B未提及。这不是冲突。
- different_drug_or_class: 来源B推荐的是同类别其他药，不是该具体药品。
- different_patient_context: 两边讨论不同患者人群。
- not_a_conflict: 两边推荐实质相同，不存在冲突。

只对 absolute_contraindication_same_patient 和 dose_adjustment（有明确数值差异时）生成题目。
其余类型标记为 no_conflict 并说明原因。
"""

DRUG_LABEL_WITH_DECISION_USER = """请对比药品说明书的安全约束与DXY临床决策的推荐，按以下分类判断冲突类型。

**关键原则**：如果说明书说"不可同瓶混合"而DXY说"分开配制"，这是相容的，不是冲突。
如果说明书说"慎用"或"从小剂量开始"，这是剂量指导，不是冲突。
只有说明书明确写"禁用"且DXY推荐该药用于相同患者人群时，才是真冲突。

**药品名称**: {drug_name}

**来源A（药品说明书）**:
- 禁忌: {contraindications}
- 儿童用药: {pediatric}
- 老年人用药: {geriatric}
- 孕妇用药: {pregnancy}
- 特殊人群: {special_populations}
- 药物相互作用: {interactions}
- 注意事项: {precautions}

**来源B（DXY临床决策中对相关药物的推荐）**:
{decision_text}

请判断来源B是否推荐了该药（或同类药物），以及该推荐是否与来源A的安全约束存在冲突。

输出JSON：
```json
{{
  "has_conflict": true/false,
  "conflict_type": "contraindication|special_population_restriction|interaction_requirement|dosing_modification|different_drug_same_class|no_conflict",
  "source_a_quote": "药瓶说明书中的原文引用",
  "source_b_quote": "DXY临床决策中的原文引用",
  "constraint_level": "contraindicated|not_recommended|use_with_caution|insufficient_data",
  "guideline_recommends": "DXY决策推荐的具体药物/方案",
  "label_says": "说明书的具体限制",
  "affected_population": "受影响的患者群体",
  "clinical_scenario_seed": "触发该冲突的场景要素",
  "confidence": 0.0-1.0
}}
```"""

# ---------- Cross-source with real international text (unchanged) ----------

CROSS_SOURCE_PDF_SYSTEM = """你是一名临床指南对比分析专家。你的任务是对比**两个真实来源的原文**，识别它们在同一个临床决策点上的具体冲突。

来源A是中国临床标准（DXY临床决策全文）。
来源B是国际临床指南（AMBOSS英文指南原文）。

约束：
1. 必须从两边**原文中分别引用完整的句子**作为证据
2. 只标记两边在同一决策点上给出**不同推荐**的真正冲突
3. 如果两边推荐相同或说不清，标记为"no_conflict"
4. 如果两边讨论的不是同一患者人群（如一边成人、一边新生儿），标记为"different_populations"
5. 仲裁依据必须具体：管辖权(jurisdiction)、证据等级(evidence_grade)、安全严重度(safety_severity)、发表日期(newer_source)等"""

CROSS_SOURCE_PDF_USER = """请对比以下两个来源的原文，判断在同一临床决策点上是否存在真实推荐冲突。

**疾病主题**: {disease_cn}

**来源A（中国临床标准 - DXY）**:
{source_a_text}

**来源B（国际指南 - AMBOSS）**:
{source_b_text}

请找出两边在以下决策点上的差异（如果有）：诊断阈值、一线用药、特殊人群限制、治疗靶目标、筛查标准、禁忌症。

输出JSON：
```json
{{
  "conflicts": [
    {{
      "has_conflict": true,
      "conflict_type": "diagnostic_threshold|first_line_therapy|special_population|treatment_target|screening|contraindication",
      "clinical_decision_point": "具体决策点（英文简短描述）",
      "source_a_quote": "来源A中的原文完整引用",
      "source_b_quote": "来源B中的原文完整引用",
      "source_a_position": "来源A的立场（一句话）",
      "source_b_position": "来源B的立场（一句话）",
      "actionable_difference": "两边的实际临床操作差异",
      "arbitration": "中国患者场景下应如何取舍及理由",
      "arbitration_basis": ["jurisdiction_match", "newer_source", "population_scope_match", "regulatory_authority"],
      "harm_level_if_wrong": "none|minor|delayed_diagnosis|inappropriate_treatment|contraindicated|severe_safety_risk",
      "confidence": 0.0-1.0
    }}
  ]
}}
```"""

# ---------- Clinical Decision mining (full text from DXY) ----------

CLINICAL_DECISION_MINER_SYSTEM = """你是一名临床指南对比分析专家。你的任务是从DXY临床决策的**全文内容**中，识别那些与国际/Western临床实践可能存在差异的具体推荐。

DXY临床决策代表中国本土的临床标准。你需要找出其中与国际通用做法（如ACC/AHA指南、ESC指南、WHO推荐等）可能在以下方面存在差异的推荐：

1. **诊断阈值**: 血压、血糖、血脂、尿酸等的诊断/治疗切点
2. **一线用药**: 首选药物的选择差异（如中国用A药，国际上首选B药）
3. **特殊人群**: 老年人、儿童、孕妇、肾功能不全等的处置差异
4. **筛查建议**: 筛查年龄、频率、方法的差异
5. **治疗目标**: 血压/血糖/血脂控制目标的差异

对每条找到的差异，你必须：
- **引用DXY原文的完整句子**作为source_a的quote
- 说明国际上可能的做法作为source_b（不需要引用，但需合理推测）
- 标注差异的临床重要性和置信度"""

CLINICAL_DECISION_MINER_USER = """以下是DXY临床决策中关于"{disease}"的内容。

**{section_name}章节原文**:
{section_text}

请从中识别最多{max_conflicts}条与国际/Western临床实践可能存在差异的推荐。输出JSON：

```json
{{
  "conflicts": [
    {{
      "conflict_type": "diagnostic_threshold|first_line_therapy|special_population|screening|treatment_target",
      "disease_topic": "具体疾病和决策点",
      "source_a_quote": "DXY临床决策中的原文完整引用（带具体数值和建议）",
      "source_b_inferred": "国际上可能的做法（合理推测，标注'推测'）",
      "actionable_difference": "两方的具体操作差异",
      "which_applies_cn": "为什么中国场景应优先DXY标准",
      "arbitration_basis": ["jurisdiction_match", "chinese_population_evidence"],
      "clinical_significance": "high|medium",
      "specific_values": {{"字段": "数值"}},
      "confidence": 0.0-1.0
    }}
  ]
}}
```"""

# ---------- Temporal Version Drift ----------

TEMPORAL_VERSION_SYSTEM = """你是一名临床指南演变分析专家。你的任务是识别同一疾病**新旧版本指南**之间的具体推荐变化。

来源A是该疾病的旧版指南摘要。来源B是新版指南摘要。你需要找出新版相对于旧版在具体推荐上的变化。

约束：
1. 必须引用两边摘要中的原文语句
2. 只标记有可操作临床差异的变化：诊断阈值改变、一线用药替换、人群限制扩展/收缩、治疗目标调整、新增禁忌/警告
3. 如果新旧版推荐实质相同，标注"no_change"
4. 优先找有具体数值变化的情况（阈值从X变Y、药物从A变B）"""

TEMPORAL_VERSION_USER = """请对比同一疾病的旧版和新版指南，识别推荐变化。

**疾病**: {disease}

**来源A（旧版指南摘要）**:
标题: {old_title}
机构: {old_maker}
日期: {old_date}
摘要: {old_summary}

**来源B（新版指南摘要）**:
标题: {new_title}
机构: {new_maker}
日期: {new_date}
摘要: {new_summary}

请找出新版相对于旧版的具体推荐变化。输出JSON：
```json
{{
  "conflicts": [
    {{
      "change_type": "diagnostic_threshold_changed|treatment_target_changed|first_line_drug_changed|population_expanded|population_restricted|new_contraindication|screening_criteria_changed|dosing_changed|no_change",
      "clinical_decision_point": "具体决策点",
      "old_value": "旧版推荐（引用摘要原文）",
      "new_value": "新版推荐（引用摘要原文）",
      "old_quote": "旧版摘要原文",
      "new_quote": "新版摘要原文",
      "actionable_difference": "临床操作的实际变化",
      "confidence": 0.0-1.0
    }}
  ]
}}
```"""

# ---------- Drug Combination Safety ----------

DRUG_COMBINATION_SYSTEM = """你是一名临床药学安全专家。你的任务是识别**两种药品联用**时的安全风险。

来源A是药品A的说明书（药物相互作用字段）。来源B是药品B的说明书（药物相互作用字段）。
患者的临床场景同时需要这两种药。

约束：
1. 必须引用药品说明书中的原文语句
2. 必须是药品A与药品B这一对药物的双向证据：来源A的引用必须明确提到药品B（或其通用名/短名），来源B的引用必须明确提到药品A（或其通用名/短名）
3. 如果原文只是泛泛列出很多药名、只说明本品与"其他药物"相互作用、或只在单侧来源提到对方药物，必须判定为has_conflict=false
4. 只标记有明确风险的相互作用：增强毒性、降低疗效、QTc延长、肾毒性、出血风险等
5. 如果说明书提到"监测"或"调整剂量"而不是"禁止联用"，标注为"monitoring_required"而非"absolute_conflict"
6. 区分严重程度：致命、严重、中度、轻微"""

DRUG_COMBINATION_USER = """请判断以下两种药品联用是否存在安全风险。

**药品A**: {drug_a_name}
**药品A说明书（药物相互作用）**: {drug_a_interactions}

**药品B**: {drug_b_name}
**药品B说明书（药物相互作用）**: {drug_b_interactions}

**临床场景**: 患者同时需要使用这两种药物。{scenario_seed}

请判断联用风险。判定规则：
- 只有当两段说明书原文都支持同一对药物（{drug_a_name} + {drug_b_name}）之间的相互作用时，has_conflict才可为true。
- source_a_quote必须是来源A中明确点名药品B或其短名的原文；source_b_quote必须是来源B中明确点名药品A或其短名的原文。
- 不要把"药物相互作用"字段中的宽泛段落、药物清单、类别清单或与第三种药物的相互作用解释成这一对药物的相互作用。
- 如果机制、风险或处理建议无法从这两段双向原文中得出，输出has_conflict=false，severity="no_risk"，confidence不超过0.4。

输出JSON：
```json
{{
  "has_conflict": true/false,
  "bilateral_evidence": true/false,
  "severity": "fatal|severe|moderate|mild|no_risk",
  "source_a_quote": "药品A说明书原文引用",
  "source_b_quote": "药品B说明书原文引用",
  "interaction_mechanism": "相互作用的药理学机制",
  "clinical_action": "应如何处理（禁止联用/调整剂量/加强监测/可以联用）",
  "affected_population": "最高风险的患者群体",
  "confidence": 0.0-1.0
}}
```"""

# ---------- LLM Judge for scoring (rule-fail escalation) ----------

JUDGE_SYSTEM = """你是一名临床答案一致性评审员。判断模型答案与金标答案在临床含义上是否一致。不要受措辞影响，只看临床实质。"""

JUDGE_USER = """金标答案：{gold}
模型答案：{model}

评判规则（严格按此执行）：
1. 核心临床决策一致（用/不用、选哪类药、目标值）= correct
2. 同义替换不扣分：「否」=「避免」=「禁用」；「ACEI」=「血管紧张素转换酶抑制剂」；中英文药名互换
3. 模型答案比金标更具体不扣分（金标「其他降压药」，模型「拉贝洛尔」= correct）
4. 数值必须与金标一致 = correct；数值不同 = wrong
5. 临床决策方向相反 = wrong（「禁用」vs「推荐使用」）
6. 模型遗漏了金标中列出的关键字段 = wrong

输出JSON：{{"verdict": "correct|wrong"}}"""

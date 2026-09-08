#!/usr/bin/env python3
"""Build the Knowledge Coverage Benchmark pilot: atomic knowledge units + ~50 questions.
Every question references required_knowledge kids (the gold evidence set) and distractor kids.
Emits data/pilot_knowledge_units.json and data/pilot_50_questions.json.
"""
import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# ---------------------------------------------------------------- knowledge units
# (kid, text, domain, jurisdiction, version, source, type)
U = [
 # --- Blood pressure ---
 ("bp-cn-dx","中国成人诊室血压≥140/90 mmHg 才能诊断为高血压。","hypertension","China","2024","中国高血压防治指南(2024)","threshold"),
 ("bp-cn-normalhigh","中国标准：诊室血压 120-139/80-89 mmHg 为正常高值血压，不属于高血压。","hypertension","China","2024","中国高血压防治指南(2024)","classification"),
 ("bp-us-dx","2017 ACC/AHA 指南将诊室血压≥130/80 mmHg 定义为高血压，130-139/80-89 为1级高血压。","hypertension","US","2017","ACC/AHA 2017","threshold"),
 ("bp-home-dx","家庭自测血压诊断高血压的阈值为≥135/85 mmHg。","hypertension","China","2024","中国高血压防治指南(2024)","threshold"),
 ("bp-cn-grade1","中国高血压1级：收缩压140-159 和/或 舒张压90-99 mmHg。","hypertension","China","2024","中国高血压防治指南(2024)","classification"),
 ("bp-cn-grade2","中国高血压2级：收缩压160-179 和/或 舒张压100-109 mmHg。","hypertension","China","2024","中国高血压防治指南(2024)","classification"),
 ("bp-cn-grade3","中国高血压3级：收缩压≥180 和/或 舒张压≥110 mmHg。","hypertension","China","2024","中国高血压防治指南(2024)","classification"),
 ("bp-cn-target-general","中国一般高血压患者降压目标为<140/90 mmHg。","hypertension","China","2024","中国高血压防治指南(2024)","treatment_target"),
 ("bp-cn-target-elderly","中国指南：≥80岁高龄老年高血压患者降压目标为<150/90 mmHg。","hypertension","China","2024","中国高血压防治指南(2024)","treatment_target"),
 ("bp-cn-target-dm","中国指南：高血压合并糖尿病、慢性肾病或冠心病者，能耐受时降压目标为<130/80 mmHg。","hypertension","China","2024","中国高血压防治指南(2024)","treatment_target"),
 ("bp-us-target-aggressive","部分国际研究/指南主张多数成人降压至<130/80 甚至收缩压<120 mmHg。","hypertension","US","2017","ACC/AHA;SPRINT","treatment_target"),
 # --- Diabetes ---
 ("dm-cn-fbg","中国标准：空腹血糖≥7.0 mmol/L 可诊断糖尿病。","diabetes","China","2020","中国2型糖尿病防治指南","threshold"),
 ("dm-cn-ifg","中国空腹血糖受损(IFG)范围为 6.1-6.9 mmol/L，正常空腹血糖<6.1。","diabetes","China","2020","中国2型糖尿病防治指南","threshold"),
 ("dm-ada-ifg","ADA标准：空腹血糖受损(IFG)为 5.6-6.9 mmol/L。","diabetes","US","2023","ADA 2023","threshold"),
 ("dm-cn-hba1c","HbA1c≥6.5% 为糖尿病诊断切点；5.7-6.4% 为糖尿病前期；<5.7% 正常。","diabetes","China","2020","中国2型糖尿病防治指南","threshold"),
 ("dm-cn-ogtt","OGTT 2小时血糖：<7.8 正常；7.8-11.0 为糖耐量减低(IGT)；≥11.1 mmol/L 诊断糖尿病。","diabetes","China","2020","中国2型糖尿病防治指南","threshold"),
 ("dm-cn-target","糖尿病一般成人 HbA1c 控制目标<7.0%，老年可放宽至<8.0%。","diabetes","China","2020","中国2型糖尿病防治指南","treatment_target"),
 ("dm-prediabetes-any","糖尿病前期含 IFG、IGT、HbA1c 5.7-6.4% 三类，满足任一即可诊断，无需三项都异常。","diabetes","China","2020","中国2型糖尿病防治指南","concept_relation"),
 # --- Urate / gout ---
 ("urate-cn-unified","中国标准：非同日两次空腹血尿酸>420 μmol/L 诊断高尿酸血症，男女统一。","gout","China","2019","中国高尿酸血症与痛风诊疗指南(2019)","threshold"),
 ("urate-old-sex","旧版/国际标准按性别分层：男性>420、女性>360 μmol/L 为高尿酸血症。","gout","International","old","旧版国际标准","threshold"),
 ("gout-target","痛风患者降尿酸治疗目标为血尿酸<360 μmol/L，有痛风石者<300 μmol/L。","gout","China","2019","中国高尿酸血症与痛风诊疗指南(2019)","treatment_target"),
 # --- Lipids ---
 ("ldl-cn-highrisk","中国血脂指南：高危患者 LDL-C 目标<2.6 mmol/L。","lipid","China","2023","中国血脂管理指南(2023)","treatment_target"),
 ("ldl-cn-veryhigh","中国血脂指南：极高危 LDL-C<1.8，超高危<1.4 mmol/L。","lipid","China","2023","中国血脂管理指南(2023)","treatment_target"),
 ("ldl-us-aggressive","美国 ACC/AHA 强调高强度他汀，极高危 LDL-C 可降至<1.8 甚至<1.4 mmol/L。","lipid","US","2018","ACC/AHA","treatment_target"),
 ("tc-abnormal","总胆固醇>5.2 mmol/L 为升高，>6.2 为显著升高。","lipid","China","2023","中国血脂管理指南(2023)","threshold"),
 # --- BMI ---
 ("bmi-cn-obese","中国成人标准：24.0-27.9 为超重，≥28.0 kg/m² 为肥胖。","obesity","China","2024","中国成人超重和肥胖防治指南","classification"),
 ("bmi-who-obese","WHO国际标准：25.0-29.9 为超重，≥30.0 kg/m² 为肥胖。","obesity","International","WHO","WHO","classification"),
 # --- Version drift ---
 ("htn-young-betablocker-new","新版中国指南：交感激活、以舒张压升高为主的中青年高血压可优先选用β受体阻滞剂。","hypertension","China","2024-new","中国高血压临床实践指南2024","temporal_update"),
 ("htn-young-old","旧版指南未单独区分中青年高血压亚型，一线推荐五大类降压药均可。","hypertension","China","old","旧版指南","temporal_update"),
 ("urate-unified-new","中国2019年后指南将高尿酸血症诊断改为男女统一>420 μmol/L（较旧版性别分层为新标准）。","gout","China","2019-new","中国高尿酸血症与痛风诊疗指南(2019)","temporal_update"),
 # --- Drug contraindications ---
 ("digoxin-preexcite","地高辛禁忌：预激综合征伴心房颤动或心房扑动者禁用。","drug","China","label","药品说明书(地高辛)","contraindication"),
 ("acei-bilateral-ras","ACE抑制剂禁忌：双侧肾动脉狭窄或孤立肾的肾动脉狭窄患者禁用。","drug","China","label","药品说明书(ACEI)","contraindication"),
 ("biapenem-valproate","比阿培南禁忌：正在使用丙戊酸钠的患者禁用（碳青霉烯类显著降低丙戊酸血药浓度、诱发癫痫）。","drug","China","label","药品说明书(比阿培南)","contraindication"),
 ("metformin-lactate","二甲双胍在严重肝肾功能不全时慎用/禁用，警惕乳酸酸中毒。","drug","China","label","药品说明书(二甲双胍)","contraindication"),
 ("digoxin-preexcite-general","房颤心室率控制常用地高辛（一般人群）。","drug","International","common","一般临床实践","treatment_target"),
 # --- Drug interactions ---
 ("digoxin-verapamil","地高辛与维拉帕米合用可使地高辛血药浓度升高50%~75%，应减量并监测血药浓度。","drug","China","label","药品说明书(地高辛)","interaction"),
 ("propranolol-cimetidine","西咪替丁抑制肝药酶，可升高普萘洛尔血药浓度并增强其作用，合用时应减量。","drug","China","label","药品说明书(普萘洛尔)","interaction"),
 ("digoxin-quinidine","奎尼丁可使地高辛血药浓度升高约一倍，两药合用应将地高辛用量减少1/2-1/3。","drug","China","label","药品说明书(地高辛)","interaction"),
 # --- Concept / attribution ---
 ("amlodipine-edema","二氢吡啶类钙拮抗剂（如氨氯地平）常见踝部水肿，为剂量相关的血管扩张副作用，非治疗失败。","concept","China","label","药品说明书/知识图谱","concept_relation"),
 ("nifedipine-flush","硝苯地平常见面部潮红、头痛，为血管扩张相关副作用，非病情加重。","concept","China","label","药品说明书(硝苯地平)","concept_relation"),
 ("primary-htn-90","原发性高血压占全部高血压90-95%，继发性(含嗜铬细胞瘤)仅占5-10%；头晕心悸为多类高血压共有的非特异性表现。","concept","China","KG","知识图谱","concept_relation"),
 ("pheo-triad","嗜铬细胞瘤典型表现为阵发性高血压伴头痛、心悸、多汗三联征，属罕见继发性高血压。","concept","International","textbook","内科学","concept_relation"),
 # --- Filler (plausible BM25 matches, not required) ---
 ("f-salt","高血压患者建议每日食盐摄入<5 g。","hypertension","China","2024","指南","treatment_target"),
 ("f-exercise","建议每周至少150分钟中等强度有氧运动。","lifestyle","International","general","指南","treatment_target"),
 ("f-acei-cough","ACEI常见副作用为持续性干咳。","drug","China","label","说明书","concept_relation"),
 ("f-statin-myopathy","他汀类常见不良反应为肌痛/肌病，需监测肌酸激酶。","drug","China","label","说明书","concept_relation"),
 ("f-dm-foot","糖尿病患者应定期进行足部检查以预防糖尿病足。","diabetes","China","2020","指南","treatment_target"),
 ("f-bp-measure","诊断高血压需非同日多次规范测量血压。","hypertension","China","2024","指南","concept_relation"),
 ("f-urate-diet","高尿酸血症患者应限制高嘌呤食物、多饮水。","gout","China","2019","指南","treatment_target"),
 ("f-metformin-first","二甲双胍是2型糖尿病一线首选口服降糖药（无禁忌时）。","drug","China","2020","指南","treatment_target"),
 ("f-ckd-bp","慢性肾脏病患者需严格控制血压并监测尿蛋白。","hypertension","China","2024","指南","treatment_target"),
 ("f-hba1c-meaning","HbA1c反映近2-3个月平均血糖水平。","diabetes","China","2020","指南","concept_relation"),
 ("f-ogtt-method","OGTT为口服75g葡萄糖后测2小时血糖。","diabetes","China","2020","指南","concept_relation"),
 ("f-bp-target-preg","妊娠期高血压的降压目标与普通人群不同。","hypertension","China","2024","指南","treatment_target"),
 ("f-lipid-tg","甘油三酯≥1.7 mmol/L 为升高。","lipid","China","2023","指南","threshold"),
 ("f-obese-waist","中心性肥胖：男性腰围≥90cm、女性≥85cm。","obesity","China","2024","指南","classification"),
 ("f-digoxin-toxic","地高辛中毒可表现为恶心、黄视、心律失常。","drug","China","label","说明书","concept_relation"),
]

UNITS = [dict(zip(["kid","text","domain","jurisdiction","version","source","type"], u)) for u in U]
UIDX = {u["kid"]: u for u in UNITS}

# ---------------------------------------------------------------- questions
# (qid, category, question, gold_answer, required_kids, distractor_kids, answer_key[list any-of], wrong_markers[list])
Q = [
 ("q01","cn_vs_intl","我40岁，体检安静时血压135/88 mmHg，平时无不适。这是高血压吗？","不是。按中国标准属于正常高值(120-139/80-89)，未达≥140/90。",
   ["bp-cn-dx","bp-cn-normalhigh"],["bp-us-dx"],["正常高值","不是高血压","不算高血压","未达","不诊断"],["1级高血压","一级高血压","stage 1","是高血压"]),
 ("q02","cn_vs_intl","网上说按美国指南我132/84就是高血压。我在中国看病，到底算不算？","按中国指南132/84为正常高值，不算高血压(阈值≥140/90)。",
   ["bp-cn-dx","bp-cn-normalhigh"],["bp-us-dx"],["正常高值","不算","不是高血压"],["1级","stage 1","是高血压"]),
 ("q03","cn_vs_intl","我是女性，血尿酸410 μmol/L，这算高尿酸血症吗？","不算，中国统一标准>420(不分性别)，410未超标。",
   ["urate-cn-unified"],["urate-old-sex"],["不算","不是高尿酸","未超","<420","不诊断"],["是高尿酸","女性>360","属于高尿酸"]),
 ("q04","cn_vs_intl","我BMI 29.5，按中国标准算肥胖吗？","算。中国≥28为肥胖(WHO≥30为肥胖，此为超重)。",
   ["bmi-cn-obese"],["bmi-who-obese"],["肥胖","算肥胖"],["超重，不算肥胖","不算肥胖","仅超重"]),
 ("q05","cn_vs_intl","我高血压合并糖尿病(高危)，复查LDL-C 2.4 mmol/L，达标了吗？","达标，中国高危目标<2.6。不必强降到美国的<1.8。",
   ["ldl-cn-highrisk"],["ldl-us-aggressive","ldl-cn-veryhigh"],["达标","已达标","<2.6"],["未达标","<1.8","需要降到1.8"]),
 ("q06","cn_vs_intl","我45岁空腹血糖5.8 mmol/L，正常吗？是糖尿病前期吗？","按中国标准正常(IFG下限6.1)，不是前期。注意勿用ADA的5.6。",
   ["dm-cn-ifg"],["dm-ada-ifg"],["正常","不是前期","未达","<6.1"],["糖尿病前期","IFG","5.6","受损"]),
 ("q07","cn_vs_intl","我父亲83岁单纯高血压，服药后148/85，达标了吗？还要加药吗？","达标，≥80岁目标<150/90，一般不需加药。",
   ["bp-cn-target-elderly"],["bp-us-target-aggressive","bp-cn-target-dm"],["达标","不需加药","<150/90"],["未达标","<130/80","需要加药"]),
 ("q08","cn_vs_intl","我是女性尿酸360，朋友说国际标准>360就偏高，我到底算不算？","按中国现行统一标准>420，360不算高尿酸血症。",
   ["urate-cn-unified"],["urate-old-sex"],["不算","不是高尿酸","正常"],["偏高","是高尿酸","女>360"]),
 ("q09","cn_vs_intl","我58岁高血压合并2型糖尿病，服药后138/86，达标了吗？","未达标，合并糖尿病目标<130/80。",
   ["bp-cn-target-dm"],["bp-cn-target-general"],["未达标","<130/80","尚未","没达标"],["已达标","<140/90 就够"]),
 ("q10","cn_vs_intl","一个人BMI 26，中国标准算什么？","超重(24-27.9)，未到肥胖(≥28)。",
   ["bmi-cn-obese"],["bmi-who-obese"],["超重"],["正常体重","肥胖"]),

 ("q11","threshold","我血压165/102 mmHg，属于几级高血压？","2级高血压(160-179/100-109)。",
   ["bp-cn-grade2"],["bp-us-dx"],["2级","二级"],["1级","3级","stage"]),
 ("q12","threshold","我血压182/108 mmHg，属于几级高血压？","3级高血压(≥180/110)。",
   ["bp-cn-grade3"],["bp-us-dx"],["3级","三级"],["1级","2级"]),
 ("q13","threshold","我血压146/94 mmHg，属于几级高血压？","1级高血压(140-159/90-99)。",
   ["bp-cn-grade1"],["bp-us-dx"],["1级","一级"],["2级","3级","正常高值"]),
 ("q14","threshold","我空腹血糖6.5 mmol/L，是糖尿病吗？是前期吗？","不是糖尿病；是空腹血糖受损(IFG 6.1-6.9)，属前期。",
   ["dm-cn-fbg","dm-cn-ifg"],["dm-ada-ifg"],["不是糖尿病","前期","IFG","受损"],["是糖尿病","确诊糖尿病"]),
 ("q15","threshold","我HbA1c 6.7%，能诊断糖尿病吗？控制目标多少？","达到诊断切点(≥6.5%，需复查)；控制目标<7.0%。",
   ["dm-cn-hba1c","dm-cn-target"],[],["6.5","可诊断","<7.0","7%"],["不是糖尿病","5.7"]),
 ("q16","threshold","我HbA1c 5.9%，正常吗？诊断糖尿病的切点是多少？","前期(5.7-6.4%)，不是糖尿病；诊断切点≥6.5%。",
   ["dm-cn-hba1c"],[],["前期","5.7-6.4","不是糖尿病","6.5"],["是糖尿病","正常，无需"]),
 ("q17","threshold","我OGTT 2小时血糖9.0 mmol/L，正常吗？是糖尿病吗？","不是糖尿病；为糖耐量减低(IGT 7.8-11.0)，属前期。",
   ["dm-cn-ogtt"],["dm-cn-fbg"],["IGT","糖耐量减低","不是糖尿病","前期"],["是糖尿病","确诊"]),
 ("q18","threshold","我OGTT 2小时血糖11.5 mmol/L，能诊断糖尿病吗？","达到糖尿病诊断切点(2h≥11.1，需复查确认)。",
   ["dm-cn-ogtt"],[],["可诊断","≥11.1","达到","糖尿病"],["不是糖尿病","IGT"]),
 ("q19","threshold","我痛风，正在降尿酸治疗，复查血尿酸385 μmol/L，达标了吗？","未达标，痛风目标<360(有痛风石<300)。",
   ["gout-target"],["urate-cn-unified"],["未达标","<360","没达标","尚未"],["已达标","<420 就够"]),
 ("q20","threshold","我总胆固醇6.5 mmol/L，算高吗？","显著升高(>6.2；>5.2即为升高)。",
   ["tc-abnormal"],[],["升高","偏高","高"],["正常","不高"]),
 ("q21","threshold","我空腹血糖6.0 mmol/L，按中国标准算糖尿病前期吗？","不算，中国IFG下限为6.1，6.0仍属正常。",
   ["dm-cn-ifg"],["dm-ada-ifg"],["正常","不算","未达","不是前期"],["前期","IFG","受损","5.6"]),

 ("q22","version_drift","按最新中国指南，交感激活、以舒张压升高为主的中青年高血压首选哪类降压药？","β受体阻滞剂(新版新增亚型推荐)。",
   ["htn-young-betablocker-new"],["htn-young-old"],["β受体阻滞剂","beta","阻滞剂"],["五大类均可","无区分"]),
 ("q23","version_drift","高尿酸血症的诊断标准，最新中国指南和旧版有何不同？现在按哪个？","现按男女统一>420(新版)，旧版男>420女>360。",
   ["urate-unified-new","urate-cn-unified"],["urate-old-sex"],["统一","420","不分性别","男女一致"],["女>360","性别分层为准"]),
 ("q24","version_drift","一位38岁男性，血压155/95、以舒张压升高为主，最新指南推荐首选什么？","β受体阻滞剂。",
   ["htn-young-betablocker-new"],["htn-young-old"],["β受体阻滞剂","阻滞剂"],["随便五大类","无推荐"]),
 ("q25","version_drift","女性尿酸400，按旧标准和新标准结论是否一致？","不一致：旧版(女>360)为高尿酸；新版统一>420则不是。以新版为准→不算。",
   ["urate-unified-new","urate-cn-unified"],["urate-old-sex"],["不一致","以新版","420","不算"],["按女>360 就是高尿酸"]),

 ("q26","drug_contra","45岁男性，预激综合征合并房颤，心室率180，能用地高辛吗？","禁用。",
   ["digoxin-preexcite"],["digoxin-preexcite-general"],["禁用","不能用","禁忌"],["可以用","常用地高辛"]),
 ("q27","drug_contra","65岁高血压，双侧肾动脉狭窄病史，能用ACEI(依那普利)吗？","禁用(双侧肾动脉狭窄禁忌)。",
   ["acei-bilateral-ras"],[],["禁用","不能用","禁忌","避免"],["可以用","一线首选"]),
 ("q28","drug_contra","正在服丙戊酸钠的癫痫患者，重症肺炎能用比阿培南吗？","禁用(碳青霉烯降低丙戊酸浓度诱发癫痫)。",
   ["biapenem-valproate"],[],["禁用","不能用","禁忌"],["可以用","可用"]),
 ("q29","drug_contra","严重肝功能不全的患者，用二甲双胍降糖安全吗？","不安全，应慎用/禁用，警惕乳酸酸中毒。",
   ["metformin-lactate"],["f-metformin-first"],["慎用","禁用","不安全","乳酸酸中毒"],["安全","首选，无碍"]),
 ("q30","drug_contra","预激综合征伴房颤的患者，网上说房颤常用地高辛控制心室率，这个患者能用吗？","不能，此人群禁用地高辛。",
   ["digoxin-preexcite"],["digoxin-preexcite-general"],["禁用","不能","禁忌"],["可以用","常用地高辛控制"]),

 ("q31","drug_interaction","慢性心衰+房颤长期服地高辛，现加维拉帕米，需要调整地高辛吗？","需减量并监测地高辛血药浓度(浓度升高50-75%)。",
   ["digoxin-verapamil"],[],["减量","监测","降低剂量","下调"],["无需调整","照常"]),
 ("q32","drug_interaction","高血压服普萘洛尔，因溃疡加西咪替丁，需要调整普萘洛尔吗？","需减量(西咪替丁升高普萘洛尔浓度)。",
   ["propranolol-cimetidine"],[],["减量","下调","调整","监测"],["无需","不用调整"]),
 ("q33","drug_interaction","服地高辛的患者加用奎尼丁，地高辛剂量怎么办？","减量1/2-1/3并监测(奎尼丁使地高辛浓度升高约一倍)。",
   ["digoxin-quinidine"],[],["减量","1/2","1/3","下调","监测"],["无需","加量"]),
 ("q34","drug_interaction","地高辛与维拉帕米合用，地高辛血药浓度会怎样变化？","升高约50-75%，需减量监测。",
   ["digoxin-verapamil"],[],["升高","增加","50","75"],["降低","不变"]),

 ("q35","concept_attribution","我高血压吃氨氯地平后脚踝水肿，是血压没控制好、病情加重了吗？要停药吗？","是钙拮抗剂常见血管扩张副作用，非病情加重；不应自行停药。",
   ["amlodipine-edema"],["primary-htn-90"],["副作用","血管扩张","不是病情加重","不应停药","不用停"],["病情加重","血压没控制好，需停药"]),
 ("q36","concept_attribution","我高血压吃硝苯地平后脸红头痛，是不是药不对、病情加重？","是硝苯地平血管扩张副作用，非病情加重。",
   ["nifedipine-flush"],[],["副作用","血管扩张","不是加重"],["病情加重","药不对，需停"]),
 ("q37","concept_attribution","我高血压，最近头晕、心悸、出汗，是不是得了嗜铬细胞瘤？","多数不是；原发性占90-95%，症状非特异，需先规范评估。",
   ["primary-htn-90"],["pheo-triad"],["多数不是","90-95","非特异","不一定","先评估"],["就是嗜铬细胞瘤","确诊嗜铬"]),
 ("q38","concept_attribution","高血压引起的头晕，是不是说明是继发性高血压？","不一定，头晕是多类高血压共有表现，原发性占绝大多数。",
   ["primary-htn-90"],["pheo-triad"],["不一定","原发性","非特异","多类"],["就是继发性","一定是继发"]),
 ("q39","concept_attribution","服氨氯地平出现踝部水肿，需要马上换降压药吗？","不必马上换，属剂量相关副作用，可咨询医生调整。",
   ["amlodipine-edema"],[],["不必马上换","副作用","咨询医生","可调整"],["立即换药","立即停药"]),

 ("q40","cn_vs_intl","按中国标准，血压120-139/80-89属于什么？","正常高值血压，不是高血压。",
   ["bp-cn-normalhigh"],["bp-us-dx"],["正常高值","不是高血压"],["1级高血压","stage 1"]),
 ("q41","threshold","糖尿病诊断的空腹血糖切点是多少？","≥7.0 mmol/L。",
   ["dm-cn-fbg"],["dm-ada-ifg"],["7.0","≥7"],["5.6","6.1 就诊断"]),
 ("q42","threshold","中国高尿酸血症的诊断阈值是多少？","男女统一>420 μmol/L。",
   ["urate-cn-unified"],["urate-old-sex"],["420","统一"],["女>360","性别分层"]),
 ("q43","cn_vs_intl","一般高血压患者(无合并症)的降压目标是多少？","<140/90 mmHg。",
   ["bp-cn-target-general"],["bp-us-target-aggressive"],["<140/90","140/90"],["<130/80 对所有人","<120"]),
 ("q44","threshold","糖尿病患者一般的HbA1c控制目标是多少？","<7.0%(老年可放宽至<8.0%)。",
   ["dm-cn-target"],[],["<7.0","7%"],["<6.5 对所有人","越低越好"]),
 ("q45","concept_attribution","空腹血糖6.5但OGTT和HbA1c都正常，能算糖尿病前期吗？","能，满足IFG(6.1-6.9)任一项即可诊断前期。",
   ["dm-prediabetes-any","dm-cn-ifg"],[],["能","可以","满足任一","是前期"],["不能","三项都要异常"]),
 ("q46","cn_vs_intl","极高危ASCVD患者，中国血脂指南的LDL-C目标是多少？","<1.8 mmol/L。",
   ["ldl-cn-veryhigh"],["ldl-cn-highrisk"],["1.8"],["2.6 就够","<1.4 才行"]),
 ("q47","drug_interaction","地高辛和奎尼丁合用为什么危险？该怎么办？","奎尼丁使地高辛浓度升高约一倍，易中毒；应减量1/2-1/3并监测。",
   ["digoxin-quinidine"],[],["升高","中毒","减量","监测"],["无相互作用","无需处理"]),
 ("q48","drug_contra","双侧肾动脉狭窄的高血压患者，为什么不能用ACEI？","ACEI在双侧肾动脉狭窄为禁忌，可致急性肾功能恶化。",
   ["acei-bilateral-ras"],[],["禁忌","禁用","肾功能","不能用"],["可以用","一线推荐"]),
 ("q49","version_drift","中青年高血压亚型，新旧中国指南推荐是否相同？","不同：旧版无区分，新版对特定亚型优选β受体阻滞剂。",
   ["htn-young-betablocker-new","htn-young-old"],[],["不同","新版","β受体阻滞剂","亚型"],["相同","无变化"]),
 ("q50","threshold","家庭自测血压诊断高血压的阈值和诊室一样吗？","不一样，家测阈值为≥135/85(诊室≥140/90)。",
   ["bp-home-dx","bp-cn-dx"],[],["135/85","不一样","不同"],["一样","都是140/90"]),
]

questions = []
for qid, cat, q, gold, req, dis, ak, wm in Q:
    req_k = [{"kid": k, "claim": UIDX[k]["text"], "source_hint": UIDX[k]["source"], "must_hit": True} for k in req]
    dis_k = [{"kid": k, "claim": UIDX[k]["text"],
              "why_distractor": {"US":"foreign guideline","International":"foreign guideline"}.get(UIDX[k]["jurisdiction"],"")
              or ("old guideline" if "old" in str(UIDX[k]["version"]) else "wrong population / irrelevant")}
             for k in dis]
    questions.append({"qid": qid, "category": cat, "question": q, "gold_answer": gold,
                      "required_knowledge": req_k, "distractor_knowledge": dis_k,
                      "answer_key": ak, "wrong_markers": wm})

DATA.mkdir(exist_ok=True)
(DATA / "pilot_knowledge_units.json").write_text(json.dumps(UNITS, ensure_ascii=False, indent=2), encoding="utf-8")
(DATA / "pilot_50_questions.json").write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding="utf-8")
# sanity: every referenced kid exists
allk = set(UIDX)
missing = [(qq["qid"], k["kid"]) for qq in questions for k in qq["required_knowledge"]+qq["distractor_knowledge"] if k["kid"] not in allk]
print(f"units={len(UNITS)} questions={len(questions)} | missing kids={missing}")
from collections import Counter
print("by category:", dict(Counter(q["category"] for q in questions)))

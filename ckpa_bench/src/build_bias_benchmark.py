#!/usr/bin/env python3
"""Generate a four-high knowledge-COVERAGE benchmark: 50 questions for each of the 4
bias kinds from 知识偏差分析.pdf — A 指南混用 / B 逻辑方向 / C 阈值记忆 / D 概念混淆.
Deterministic (templates + value grids + computed gold + required_knowledge IDs). No LLM.

Each item: {qid, kind, subkind, question, gold_answer, required_knowledge[], distractor_knowledge[],
            answer_key[], wrong_markers[]}. Same coverage schema as data/pilot_50_questions.json.
Outputs: data/bias_benchmark_units.json , data/bias_benchmark_200.json
"""
import json
from pathlib import Path
DATA = Path(__file__).resolve().parent.parent / "data"

# ---------------- knowledge units (atomic claims; China + US/old distractors + filler) ----------
U = [
 # BP
 ("bp-cn-dx","中国：诊室血压≥140/90 mmHg 诊断高血压。","China","threshold"),
 ("bp-cn-normalhigh","中国：诊室血压120-139/80-89 mmHg 为正常高值，非高血压。","China","classification"),
 ("bp-cn-normal","中国：诊室血压<120/80 mmHg 为正常血压。","China","classification"),
 ("bp-us-dx","2017 ACC/AHA：血压≥130/80 mmHg 即为高血压；130-139/80-89 为1级。","US","threshold"),
 ("bp-cn-g1","中国高血压1级：140-159/90-99 mmHg。","China","classification"),
 ("bp-cn-g2","中国高血压2级：160-179/100-109 mmHg。","China","classification"),
 ("bp-cn-g3","中国高血压3级：≥180/110 mmHg。","China","classification"),
 ("bp-grade-rule","收缩压与舒张压分属不同级别时取较高者。","China","concept_relation"),
 ("bp-t-general","中国：一般高血压降压目标<140/90 mmHg。","China","treatment_target"),
 ("bp-t-elderly","中国：≥80岁高龄目标<150/90 mmHg。","China","treatment_target"),
 ("bp-t-dm","中国：合并糖尿病/CKD/冠心病目标<130/80 mmHg。","China","treatment_target"),
 ("bp-us-aggressive","国际：多数成人降至<130/80 甚至收缩压<120 mmHg。","US","treatment_target"),
 ("bp-hypotension","成人血压<90/60 mmHg 可视为低血压。","China","threshold"),
 ("bp-home-dx","家庭自测血压诊断阈值≥135/85 mmHg。","China","threshold"),
 # DM
 ("dm-fbg-dx","中国：空腹血糖≥7.0 mmol/L 诊断糖尿病。","China","threshold"),
 ("dm-ifg-cn","中国：空腹血糖受损(IFG)6.1-6.9；正常<6.1 mmol/L。","China","threshold"),
 ("dm-ifg-ada","ADA：空腹血糖受损5.6-6.9 mmol/L。","US","threshold"),
 ("dm-hba1c","HbA1c≥6.5%糖尿病；5.7-6.4%前期；<5.7%正常。","China","threshold"),
 ("dm-ogtt","OGTT2h<7.8正常；7.8-11.0 IGT；≥11.1 糖尿病(mmol/L)。","China","threshold"),
 ("dm-target","糖尿病HbA1c控制目标<7.0%，老年可放宽<8.0%。","China","treatment_target"),
 ("dm-pre-any","糖尿病前期含IFG/IGT/HbA1c5.7-6.4%，满足任一即可诊断。","China","concept_relation"),
 # urate / gout
 ("urate-cn","中国：非同日两次空腹血尿酸>420 μmol/L 诊断高尿酸血症，男女统一。","China","threshold"),
 ("urate-old","旧/国际：男>420、女>360 μmol/L 为高尿酸血症。","International","threshold"),
 ("gout-target","痛风降尿酸目标<360 μmol/L，有痛风石<300。","China","treatment_target"),
 # lipid
 ("ldl-high","中国：高危LDL-C目标<2.6 mmol/L。","China","treatment_target"),
 ("ldl-vhigh","中国：极高危<1.8、超高危<1.4 mmol/L。","China","treatment_target"),
 ("ldl-us","美国：高强度他汀，极高危LDL-C<1.8甚至<1.4。","US","treatment_target"),
 ("tc-abn","总胆固醇>5.2升高，>6.2显著升高 mmol/L。","China","threshold"),
 # BMI
 ("bmi-cn","中国：24-27.9超重，≥28.0 kg/m²肥胖。","China","classification"),
 ("bmi-who","WHO：25-29.9超重，≥30.0 kg/m²肥胖。","International","classification"),
 # version drift
 ("htn-young-new","新版中国指南：交感激活、舒张压升高为主的中青年高血压可优先β受体阻滞剂。","China","temporal_update"),
 ("htn-young-old","旧版：未区分中青年高血压亚型，五大类均可。","China","temporal_update"),
 ("urate-new","中国2019年后统一>420(不分性别)为新标准，取代旧版性别分层。","China","temporal_update"),
 # logic
 ("logic-rise","化验值升高代表控制变差，不是改善。","China","concept_relation"),
 ("logic-cross7","空腹血糖越过≥7.0达糖尿病切点，属恶化。","China","concept_relation"),
 ("logic-normalize","化验值回到正常范围＝真实改善/正常化。","China","concept_relation"),
 ("logic-trend","应同时看绝对水平与变化趋势。","China","concept_relation"),
 ("logic-overtreat","降压过度伴体位性头晕提示药物过量，应减量。","China","concept_relation"),
 # concept / attribution
 ("cc-dhp-edema","二氢吡啶类钙拮抗剂(氨氯地平/硝苯地平)常见踝部水肿/潮红，为剂量相关副作用，非病情加重。","China","concept_relation"),
 ("cc-acei-cough","ACEI常见副作用为干咳，非病情加重。","China","concept_relation"),
 ("cc-primary90","原发性高血压占90-95%，继发性仅5-10%。","China","concept_relation"),
 ("cc-nonspecific","头晕/心悸为多类高血压共有的非特异性表现。","China","concept_relation"),
 ("cc-pheo","嗜铬细胞瘤为罕见继发性，典型为阵发性高血压+头痛心悸多汗三联征。","International","concept_relation"),
 ("cc-not-stop","出现药物常见副作用不应自行停药，应就医评估。","China","concept_relation"),
 # filler
 ("f-salt","高血压建议每日食盐<5 g。","China","treatment_target"),
 ("f-exercise","每周≥150分钟中等强度有氧运动。","International","treatment_target"),
 ("f-bp-repeat","诊断高血压需非同日多次规范测量。","China","concept_relation"),
 ("f-hba1c-mean","HbA1c反映近2-3个月平均血糖。","China","concept_relation"),
 ("f-metformin","二甲双胍为2型糖尿病一线口服降糖药。","China","treatment_target"),
 ("f-statin-myo","他汀常见不良反应为肌痛。","China","concept_relation"),
]
UNITS=[dict(zip(["kid","text","jurisdiction","type"],u)) for u in U]
KID={u["kid"] for u in UNITS}

items=[]
def add(kind,sub,q,gold,req,dis,ak,wm):
    for k in req+dis: assert k in KID, k
    items.append({"qid":f"{kind}-{len([x for x in items if x['kind']==kind])+1:03d}","kind":kind,"subkind":sub,
        "question":q,"gold_answer":gold,
        "required_knowledge":[{"kid":k,"claim":next(u['text'] for u in UNITS if u['kid']==k),"must_hit":True} for k in req],
        "distractor_knowledge":[{"kid":k,"claim":next(u['text'] for u in UNITS if u['kid']==k),
            "why":"foreign guideline" if next(u['jurisdiction'] for u in UNITS if u['kid']==k) in("US","International") else "old/other"} for k in dis],
        "answer_key":ak,"wrong_markers":wm})

SCN=["体检时","安静休息后","近期","门诊复查","单位体检"]
# ================= A 指南混用 (50) =================
def bp_dx_gold(s,d):
    if s>=140 or d>=90: return "高血压"
    if s>=120 or d>=80: return "正常高值"
    return "正常"
for i,(s,d) in enumerate([(135,88),(138,85),(132,84),(128,82),(139,89),(145,92),(150,95),(142,91),(136,86),(125,79),(133,87),(148,88)]):
    g=bp_dx_gold(s,d); ak=["正常高值","不是高血压","不算高血压"] if g=="正常高值" else (["高血压"] if g=="高血压" else ["正常"])
    wm=["1级高血压","stage 1","是高血压"] if g=="正常高值" else []
    add("A","bp_diagnosis",f"我{40+i}岁，{SCN[i%5]}测血压{s}/{d} mmHg。这算高血压吗？",
        f"{g}（中国≥140/90才诊断）",["bp-cn-dx","bp-cn-normalhigh"],["bp-us-dx"],ak,wm)
def bp_grade(s,d):
    def g(x,lo,hi): return lo<=x<=hi
    lv=0
    for val,(a,b,c) in [(s,(1,2,3)),(d,(1,2,3))]: pass
    sg=1 if 140<=s<=159 else 2 if 160<=s<=179 else 3 if s>=180 else 0
    dg=1 if 90<=d<=99 else 2 if 100<=d<=109 else 3 if d>=110 else 0
    return max(sg,dg)
for i,(s,d) in enumerate([(146,94),(165,102),(182,108),(155,98),(172,110),(168,105),(158,96),(190,100)]):
    lv=bp_grade(s,d); add("A","bp_grade",f"我血压{s}/{d} mmHg，按中国指南属于几级高血压？",
        f"{lv}级高血压",["bp-cn-g1","bp-cn-g2","bp-cn-g3","bp-grade-rule"],["bp-us-dx"],[f"{lv}级"],[f"{lv-1}级" if lv>1 else "正常高值"])
for i,(age,cond,s,d,tgt,req) in enumerate([
    (83,"单纯高血压",145,85,"<150/90","bp-t-elderly"),(58,"高血压合并糖尿病",138,86,"<130/80","bp-t-dm"),
    (52,"单纯高血压",135,85,"<140/90","bp-t-general"),(82,"单纯高血压",148,88,"<150/90","bp-t-elderly"),
    (60,"高血压合并慢性肾病",132,84,"<130/80","bp-t-dm"),(48,"单纯高血压",145,92,"<140/90","bp-t-general"),
    (85,"高龄高血压",149,89,"<150/90","bp-t-elderly"),(55,"高血压合并冠心病",136,82,"<130/80","bp-t-dm")]):
    达=("已达标" if ((s<int(tgt.split('/')[0][1:]) and d<int(tgt.split('/')[1]))) else "未达标")
    add("A","bp_target",f"我{age}岁，{cond}，服药后血压{s}/{d} mmHg。达标了吗？",
        f"{达}，目标{tgt}",[req],["bp-us-aggressive"],[达],["未达标" if 达=="已达标" else "已达标"])
for i,(sex,val) in enumerate([("女",410),("男",430),("女",360),("女",400),("男",425),("女",418),("男",415)]):
    g="属于高尿酸血症" if val>420 else "不算高尿酸血症"
    add("A","urate_unified",f"我是{sex}性，血尿酸{val} μmol/L，这算高尿酸血症吗？",
        f"{g}（中国统一>420）",["urate-cn"],["urate-old"],["属于高尿酸" if val>420 else "不算"],["女>360"] )
for i,bmi in enumerate([29.5,26.0,28.4,24.5,31.0,27.0,23.5]):
    g="肥胖" if bmi>=28 else ("超重" if bmi>=24 else "正常")
    add("A","bmi",f"我BMI是{bmi} kg/m²，按中国标准算肥胖吗？",f"{g}（中国≥28为肥胖）",["bmi-cn"],["bmi-who"],[g],["超重，不算肥胖"] if g=="肥胖" else [])
for i,(risk,val,req) in enumerate([("高危",2.5,"ldl-high"),("高危",2.8,"ldl-high"),("极高危",1.6,"ldl-vhigh"),("极高危",2.0,"ldl-vhigh"),("高危",2.4,"ldl-high")]):
    thr=2.6 if risk=="高危" else 1.8; 达="已达标" if val<thr else "未达标"
    add("A","ldl_target",f"我{risk}患者，复查LDL-C {val} mmol/L，达标了吗？",f"{达}，{risk}目标<{thr}",[req],["ldl-us"],[达],["未达标" if 达=="已达标" else "已达标"])

# ================= B 逻辑方向 (50) =================
for i,(t,b,a,u) in enumerate([("空腹血糖",6.0,7.3,"mmol/L"),("空腹血糖",5.8,7.1,"mmol/L"),("HbA1c",6.0,7.2,"%"),
    ("空腹血糖",6.5,8.0,"mmol/L"),("血尿酸",380,460,"μmol/L"),("LDL-C",2.4,3.2,"mmol/L"),
    ("总胆固醇",5.0,6.5,"mmol/L"),("HbA1c",6.8,8.5,"%"),("空腹血糖",6.2,7.5,"mmol/L"),
    ("血压收缩压",135,158,"mmHg"),("HbA1c",5.9,6.8,"%"),("空腹血糖",6.0,6.8,"mmol/L")]):
    add("B","rise_not_improve",f"我{t}以前是{b}{u}，最近查变成{a}{u}。这说明改善了吗？",
        "没有改善，是升高/恶化",["logic-rise"]+(["logic-cross7"] if t=="空腹血糖" and a>=7 else []),[],
        ["没有改善","恶化","升高","变差"],["改善","好转","变好"])
for i,(t,val,u) in enumerate([("空腹血糖",5.5,"mmol/L"),("HbA1c",5.5,"%"),("空腹血糖",5.8,"mmol/L"),("空腹血糖",5.0,"mmol/L"),
    ("HbA1c",5.3,"%"),("空腹血糖",5.9,"mmol/L"),("HbA1c",5.6,"%"),("空腹血糖",5.4,"mmol/L")]):
    add("B","normalize",f"我去年被诊断糖尿病前期，这次复查{t} {val}{u}(已在正常范围)。这算改善了吗？",
        "算真实改善，已恢复正常范围",["logic-normalize"],[],["改善","正常化","恢复正常","好转"],["仍是前期","未改善"])
for i,(t,b,a,u) in enumerate([("HbA1c",8.5,6.9,"%"),("HbA1c",9.0,6.8,"%"),("空腹血糖",9.0,6.5,"mmol/L"),
    ("HbA1c",8.0,6.7,"%"),("血尿酸",520,350,"μmol/L"),("LDL-C",3.8,2.4,"mmol/L"),("HbA1c",7.8,6.5,"%")]):
    add("B","deny_improve",f"我确诊后坚持饮食运动，{t}从{b}{u}降到{a}{u}。这算改善吗？",
        "算明显改善",["logic-normalize"],[],["改善","好转","明显改善","达标"],["没有改善","无改善"])
for i,(b1,b2) in enumerate([(158,96,96,56),(150,95,95,58),(160,100,100,60),(145,92,88,54),(155,98,92,55),
    (162,102,98,58),(148,90,90,55),(165,105,94,56),(152,94,85,52),(158,100,96,60)][:10] if False else
    [(158,"96→96/56"),(150,"95→95/58"),(160,"100→98/60"),(145,"92→88/54"),(155,"98→92/55"),
     (162,"102→98/58"),(148,"90→90/55"),(165,"105→94/56"),(152,"94→85/52"),(158,"100→96/60")]):
    add("B","bp_direction",f"我高血压，吃降压药后血压变化为 {b2} mmHg，还有起身头晕。这样降得合适吗？",
        "降得偏低/过度，有低血压风险，应就医减量",["logic-overtreat","bp-hypotension"],[],
        ["偏低","过度","过低","减量","低血压"],["合适","正常"])
for i,seq in enumerate(["9.0→8.0→7.2→6.8","10→9→8→7.2","8.5→8.0→7.5→7.0","9.5→8.5→7.8→6.9","8.8→8.0→7.4→6.8"]):
    add("B","trend_state",f"我这半年HbA1c依次是{seq}%。这个趋势怎么看？",
        "持续下降的良好趋势，最新已接近/达到<7.0%目标",["logic-trend","dm-target"],[],["改善","下降","达标","好转"],["恶化","变差"])

# ================= C 阈值记忆 (50) =================
for i,val in enumerate([5.9,6.3,6.7,5.5,6.5,5.6,6.9,6.0,5.8,7.0]):
    g="糖尿病" if val>=6.5 else ("糖尿病前期" if val>=5.7 else "正常")
    add("C","hba1c",f"我HbA1c是{val}%，属于正常、糖尿病前期还是糖尿病？诊断切点是多少？",
        f"{g}（前期5.7-6.4，糖尿病≥6.5%）",["dm-hba1c"],[],[g,"6.5"],[])
for i,val in enumerate([5.8,6.0,6.5,7.0,6.9,5.9,6.1,7.3,6.3,5.6,6.8,6.05]):
    g="糖尿病" if val>=7.0 else ("空腹血糖受损" if val>=6.1 else "正常")
    add("C","fbg",f"我空腹血糖{val} mmol/L，按中国标准属于正常、糖尿病前期还是糖尿病？",
        f"{g}（中国IFG 6.1-6.9，糖尿病≥7.0；勿用ADA的5.6）",["dm-ifg-cn","dm-fbg-dx"],["dm-ifg-ada"],
        [g if g!="空腹血糖受损" else "糖尿病前期"],["5.6","前期"] if g=="正常" else [])
for i,val in enumerate([9.0,11.5,7.5,11.1,8.0,10.5,7.8,12.0,9.5,11.0]):
    g="糖尿病" if val>=11.1 else ("糖耐量减低" if val>=7.8 else "正常")
    add("C","ogtt",f"我OGTT 2小时血糖{val} mmol/L，属于什么？能否诊断糖尿病？",
        f"{g}（IGT 7.8-11.0，糖尿病≥11.1）",["dm-ogtt"],[],[g if g!="糖耐量减低" else "糖耐量减低"],["糖尿病" if g!="糖尿病" else ""])
for i,(s,d) in enumerate([(146,94),(165,102),(182,108),(155,98),(172,110),(140,90),(160,100),(178,105)]):
    lv=bp_grade(s,d); add("C","bp_threshold",f"血压{s}/{d} mmHg 在中国指南里是几级高血压？",
        f"{lv}级",["bp-cn-g1","bp-cn-g2","bp-cn-g3"],["bp-us-dx"],[f"{lv}级"],[])
for i,(fbg,ogtt,hba1c) in enumerate([("6.5","正常","正常"),("正常","9.0","正常"),("正常","正常","5.9"),
    ("6.3","8.5","正常"),("5.9","正常","正常")]):
    add("C","prediabetes",f"我空腹血糖{fbg}、OGTT2h {ogtt}、HbA1c {hba1c}。能算糖尿病前期吗？",
        "能（满足IFG/IGT/HbA1c任一即可）" if (fbg not in('正常','5.9') or ogtt not in('正常',) or hba1c not in('正常',)) else "不算",
        ["dm-pre-any","dm-ifg-cn"],["dm-ifg-ada"],["能" if (fbg not in('正常','5.9') or ogtt!='正常' or hba1c!='正常') else "不算"],["三项都要"])

# ================= D 概念混淆 (50) =================
DRUG_SE=[("氨氯地平","脚踝水肿"),("硝苯地平","脸红、头痛"),("氨氯地平","下肢水肿"),("非洛地平","踝部水肿"),
    ("硝苯地平","面部潮红"),("氨氯地平","头痛"),("左旋氨氯地平","脚肿"),("硝苯地平","心悸、脸红")]*2
for i,(drug,se) in enumerate(DRUG_SE[:16]):
    add("D","side_effect_vs_worsening",f"我高血压吃{drug}后出现{se}。是不是病情加重了？要停药吗？",
        f"{se}是{drug}(二氢吡啶类钙拮抗剂)常见血管扩张副作用，非病情加重；不应自行停药",
        ["cc-dhp-edema","cc-not-stop"],[],["副作用","不是病情加重","不应停药","不用停药"],["病情加重","需停药"])
for i,sym in enumerate(["头晕、心悸、出汗","阵发性头痛、心悸","心悸、多汗","头晕、心悸","出汗、心慌",
    "头痛、心悸、面色苍白","心悸、乏力","头晕、出汗","心悸、焦虑","阵发性心悸、多汗"]):
    add("D","secondary_overattribution",f"我有高血压，最近有{sym}。是不是得了嗜铬细胞瘤这种病？",
        "多数不是；原发性占90-95%，症状非特异，需先规范评估",
        ["cc-primary90","cc-nonspecific"],["cc-pheo"],["多数不是","不一定","90-95","非特异","先评估"],["就是嗜铬细胞瘤","确诊嗜铬"])
for i,sym in enumerate(["头晕","头痛","耳鸣","乏力","视物模糊","颈部不适","心悸","失眠","肩颈酸痛","胸闷"]):
    add("D","symptom_attribution",f"高血压引起的{sym}，是不是说明是继发性高血压？",
        "不一定，该症状是多类高血压共有表现，原发性占绝大多数",
        ["cc-nonspecific","cc-primary90"],["cc-pheo"],["不一定","原发性","非特异","不能仅凭"],["就是继发性","一定是继发"])
for i,(drug,se) in enumerate([("依那普利","干咳"),("卡托普利","持续干咳"),("赖诺普利","刺激性咳嗽"),
    ("贝那普利","干咳"),("培哚普利","咳嗽"),("福辛普利","干咳"),("雷米普利","夜间干咳"),("咪达普利","干咳")]):
    add("D","acei_cough",f"我高血压吃{drug}后一直干咳。是不是病情加重、要马上换药停药？",
        f"干咳是{drug}(ACEI)常见副作用，非病情加重；可咨询医生换用ARB，不应自行停药",
        ["cc-acei-cough","cc-not-stop"],[],["副作用","不是病情加重","咨询医生","不应自行停药"],["病情加重","立即停药"])

# ------ top up each kind to exactly 50 ------
# A +3 (bp diagnosis)
for s,d in [(137,84),(141,86),(129,81)]:
    g=bp_dx_gold(s,d); ak=["正常高值","不是高血压"] if g=="正常高值" else (["高血压"] if g=="高血压" else ["正常"])
    add("A","bp_diagnosis",f"我{45}岁，体检测血压{s}/{d} mmHg。这算高血压吗？",f"{g}（中国≥140/90）",
        ["bp-cn-dx","bp-cn-normalhigh"],["bp-us-dx"],ak,["1级高血压"] if g=="正常高值" else [])
# B +8 (rise_not_improve + normalize)
for t,b,a,u in [("血尿酸",400,470,"μmol/L"),("HbA1c",6.2,7.4,"%"),("空腹血糖",6.1,7.2,"mmol/L"),("LDL-C",2.2,3.0,"mmol/L")]:
    add("B","rise_not_improve",f"我{t}以前{b}{u}，现在{a}{u}，这说明好转了吗？","没有改善，是升高/恶化",
        ["logic-rise"],[],["没有改善","恶化","升高"],["改善","好转"])
for t,val,u in [("空腹血糖",5.2,"mmol/L"),("HbA1c",5.4,"%"),("空腹血糖",5.6,"mmol/L"),("HbA1c",5.5,"%")]:
    add("B","normalize",f"我曾诊断糖尿病前期，复查{t} {val}{u}已正常。算改善了吗？","算真实改善/正常化",
        ["logic-normalize"],[],["改善","正常化","恢复正常"],["仍是前期"])
# C +5 (fbg/hba1c)
for val in [6.4,6.6]:
    g="糖尿病前期" if val<6.5 else "糖尿病"
    add("C","hba1c",f"HbA1c {val}%，属于什么？",f"{g}",["dm-hba1c"],[],[g,"6.5"],[])
for val in [5.7,6.2,7.1]:
    g="糖尿病" if val>=7.0 else ("空腹血糖受损" if val>=6.1 else "正常")
    add("C","fbg",f"空腹血糖{val} mmol/L，按中国标准是什么？",f"{g}（IFG 6.1-6.9）",
        ["dm-ifg-cn","dm-fbg-dx"],["dm-ifg-ada"],[g if g!="空腹血糖受损" else "糖尿病前期"],["5.6"] if g=="正常" else [])
# D +6 (secondary + acei)
for sym in ["头晕、心悸","出汗、心慌","阵发性头痛"]:
    add("D","secondary_overattribution",f"我高血压伴{sym}，是不是嗜铬细胞瘤？","多数不是，原发性占90-95%",
        ["cc-primary90","cc-nonspecific"],["cc-pheo"],["多数不是","不一定","原发性"],["确诊嗜铬"])
for drug in ["依那普利","卡托普利","贝那普利"]:
    add("D","acei_cough",f"吃{drug}后干咳，要停药吗？",f"干咳是{drug}(ACEI)副作用，不应自行停药，可换ARB",
        ["cc-acei-cough","cc-not-stop"],[],["副作用","不应自行停药","换ARB","咨询医生"],["立即停药"])

from collections import Counter
def finalize():
    by={}
    for it in items: by.setdefault(it["kind"],[]).append(it)
    out=[]
    for k in ["A","B","C","D"]:
        lst=by.get(k,[])[:50]
        for j,it in enumerate(lst): it["qid"]=f"{k}-{j+1:03d}"
        out.extend(lst)
    return out,{k:len(by.get(k,[])) for k in ["A","B","C","D"]}
final,counts=finalize()
DATA.mkdir(exist_ok=True)
(DATA/"bias_benchmark_units.json").write_text(json.dumps(UNITS,ensure_ascii=False,indent=2),encoding="utf-8")
(DATA/"bias_benchmark_200.json").write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding="utf-8")
print("generated per kind:",counts,"| total:",len(final),"| units:",len(UNITS))
print("subkinds:",dict(Counter(x['subkind'] for x in final)))

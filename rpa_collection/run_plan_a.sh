#!/bin/bash
# run_plan_a.sh — 方案A：5 个模型全部通过 GEO 后端采集
#
# 千问 / DeepSeek / 豆包 -> 统一模型网关（模型原生联网搜索）
# Kimi / 元宝(混元)      -> GEO 后端预配置的 RPA 任务
#
# 注意：这与 model_response/run.py 的旧数据不是同一实验条件
#       （旧的是客户端豆包搜索拼进 prompt），不要混合分析。

set -euo pipefail

cd "$(dirname "$0")"

GEO_BASE_URL="${GEO_BASE_URL:-http://127.0.0.1:6011}"

echo "========================================="
echo "方案A：5 个模型采集（全部经 GEO 后端）"
echo "========================================="

# Step 1: 确认 GEO 后端在跑
echo ""
echo "[1/2] 检查 GEO 后端 ${GEO_BASE_URL} ..."
if curl -fsS -m 10 -X POST "${GEO_BASE_URL}/api/llm/deepseek/info" \
        -H 'Content-Type: application/json' > /dev/null 2>&1; then
    echo "✓ GEO 后端已运行"
else
    echo "✗ GEO 后端没响应。请在另一个终端启动它："
    echo "    cd <GeoProject>/geo_server && ./mvnw spring-boot:run"
    echo "  看到 'Tomcat started on port 6011' 后再重跑本脚本。"
    exit 1
fi

# Step 2: 采集
echo ""
echo "[2/2] 开始采集 50 题 x 5 模型..."
echo "      单题最长可能等 920 秒，全程会比较久。"
.venv/bin/python trigger_geo_all.py --resume "$@"

echo ""
echo "========================================="
echo "结果文件："
ls -lh results/*.jsonl

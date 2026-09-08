#!/bin/bash
# Test the chat_ui_task.py script locally with your existing Chromium

export PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH="$HOME/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"

export API_CUSTOM_INPUT='{"platform":"doubao","questions":[{"question_id":"Q001","cancer_type":"肺癌骨转移","question_type":"治疗目标","question":"肺癌骨转移有可能治好吗？"}]}'

# Run the script
.venv/bin/python chat_ui_task.py

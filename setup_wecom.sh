#!/bin/bash
set -e

echo "🚀 tmux-agent 企业微信通知配置设置"

# 检查必需文件
if [ ! -f ".env" ]; then
    echo "❌ 未找到 .env 文件，请先运行配置"
    exit 1
fi

if [ ! -f "agent-config.yaml" ]; then
    echo "❌ 未找到 agent-config.yaml 文件"
    exit 1
fi

echo "✅ 配置文件检查通过"

# 加载环境变量
source .env

echo "📊 当前配置:"
echo "   Corp ID: ${WECOM_CORP_ID}"
echo "   Agent ID: ${WECOM_AGENT_ID}"
echo "   Target: ${WECOM_TOUSER:-@all}"

# 显示当前出口IP
echo ""
echo "🌐 网络出口检查:"
DIRECT_IP=$(curl -s https://ipinfo.io/ip || echo "获取失败")
echo "   直连出口IP: $DIRECT_IP"

if command -v proxychains4 >/dev/null 2>&1; then
    PROXY_IP=$(proxychains4 -q curl -s https://ipinfo.io/ip 2>/dev/null || echo "获取失败")
    echo "   代理出口IP: $PROXY_IP"
fi

echo ""
echo "⚠️  重要提醒:"
echo "   需要在企业微信管理后台添加以下IP到白名单:"
echo "   - 直连IP: $DIRECT_IP"
if [ "$PROXY_IP" != "获取失败" ] && [ "$PROXY_IP" != "$DIRECT_IP" ]; then
    echo "   - 代理IP: $PROXY_IP"
fi
echo ""
echo "   白名单配置路径:"
echo "   https://work.weixin.qq.com → 应用管理 → 自建应用($WECOM_AGENT_ID) → 开发管理 → 接口IP白名单"

echo ""
echo "🧪 运行烟雾测试:"
.venv/bin/python scripts/wecom_smoke_test.py

echo ""
echo "📋 下一步操作:"
echo "1. 添加IP白名单后重新运行: ./setup_wecom.sh"
echo "2. 启动tmux-agent: .venv/bin/python -m src.tmux_agent.main --config agent-config.yaml"
echo "3. 测试staging policy: 在tmux窗口中输入 'test-wecom-notification'"
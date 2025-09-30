#!/usr/bin/env python3
"""简单的webhook测试"""
import os
from dotenv import load_dotenv
from src.tmux_agent.notify import Notifier, NotificationMessage

load_dotenv()

webhook_url = os.getenv("WECOM_WEBHOOK_URL")
if not webhook_url:
    print("❌ 请先设置WECOM_WEBHOOK_URL环境变量")
    print("🔧 编辑 .env 文件，添加你的群机器人webhook地址")
    exit(1)

print(f"🧪 测试企业微信群机器人...")
print(f"📡 Webhook: {webhook_url[:50]}...")

try:
    notifier = Notifier(channel="wecom_webhook")
    message = NotificationMessage(
        title="🤖 tmux-agent 群机器人测试",
        body="✅ 群机器人配置成功！\n\n🎉 现在可以接收tmux-agent的通知了\n📱 无需担心IP白名单问题"
    )
    notifier.send(message)
    print("✅ 测试消息发送成功！")
    print("📱 请检查企业微信群是否收到消息")
except Exception as e:
    print(f"❌ 测试失败: {e}")
    print("🔧 请检查webhook地址是否正确")
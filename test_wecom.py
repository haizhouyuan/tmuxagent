#!/usr/bin/env python3
"""测试企业微信通知配置"""
import os
from dotenv import load_dotenv
from src.tmux_agent.notify import Notifier, NotificationMessage

# 加载环境变量
load_dotenv()

# 临时禁用代理设置
proxy_vars = ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy']
original_proxies = {}
for var in proxy_vars:
    if var in os.environ:
        original_proxies[var] = os.environ[var]
        del os.environ[var]

def test_wecom_notification():
    """测试企业微信通知发送"""
    # 检查必需的环境变量
    required_vars = ['WECOM_CORP_ID', 'WECOM_AGENT_ID', 'WECOM_APP_SECRET']
    for var in required_vars:
        if not os.getenv(var):
            print(f"❌ 缺少环境变量: {var}")
            return False

    print("✅ 环境变量检查通过")

    try:
        # 创建通知器
        notifier = Notifier(channel="wecom_app")

        # 创建测试消息
        message = NotificationMessage(
            title="tmux-agent 测试通知",
            body="这是一条来自 tmux-agent 的测试消息。\n\n**配置成功！**\n\n现在可以接收来自调度器的通知了。"
        )

        # 发送测试消息
        print("📤 正在发送测试消息到企业微信...")
        notifier.send(message)
        print("✅ 测试消息发送成功！请检查你的企业微信应用")
        return True

    except Exception as e:
        print(f"❌ 发送失败: {e}")
        return False

if __name__ == "__main__":
    success = test_wecom_notification()
    exit(0 if success else 1)
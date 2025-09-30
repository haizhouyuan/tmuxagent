#!/usr/bin/env python3
"""
企业微信群机器人设置和测试脚本
无需IP白名单，适用于可变IP环境
"""
import os
from dotenv import load_dotenv
import httpx

def test_webhook(webhook_url: str):
    """测试webhook连接"""
    if not webhook_url:
        print("❌ 请先配置WECOM_WEBHOOK_URL")
        return False

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "content": """**🤖 tmux-agent 群机器人测试**

> **测试目的**: 验证企业微信群机器人通知
> **优势**: 无需IP白名单，适用于可变IP环境
> **状态**: ✅ 连接成功

---

**下一步操作:**
1. 将agent配置中的notify改为: `wecom_webhook`
2. 启动tmux-agent开始接收通知

*此消息来自群机器人测试脚本*"""
        }
    }

    try:
        with httpx.Client(timeout=10.0, trust_env=False) as client:
            response = client.post(webhook_url, json=payload)
            response.raise_for_status()
            data = response.json()

            if data.get("errcode") == 0:
                print("✅ 群机器人测试成功！")
                print("📱 请检查企业微信群是否收到测试消息")
                return True
            else:
                print(f"❌ 群机器人测试失败: {data}")
                return False

    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return False

def main():
    print("🤖 企业微信群机器人配置向导")
    print("=" * 50)

    load_dotenv()
    webhook_url = os.getenv("WECOM_WEBHOOK_URL")

    if not webhook_url:
        print("""
📋 设置步骤:

1. 在企业微信群中添加机器人:
   - 打开企业微信群
   - 点击群设置 → 机器人 → 添加机器人
   - 选择"自定义机器人"
   - 复制Webhook地址

2. 配置环境变量:
   编辑 .env 文件，设置:
   WECOM_WEBHOOK_URL=你的webhook地址

3. 重新运行此脚本测试连接

💡 群机器人的优势:
   - ✅ 无需IP白名单
   - ✅ 适用于可变IP环境
   - ✅ 配置简单
   - ✅ 支持Markdown格式
""")
        return

    print(f"🔍 检测到webhook配置")
    print(f"📡 Webhook URL: {webhook_url[:50]}...")

    print(f"\n🧪 开始测试连接...")
    if test_webhook(webhook_url):
        print(f"""
🎉 群机器人配置成功！

📝 使用方法:
1. 修改 agent-config.yaml:
   notify: wecom_webhook

2. 启动 tmux-agent:
   .venv/bin/python -m src.tmux_agent.main --config agent-config.yaml

3. 所有通知将发送到企业微信群

✨ 无需担心IP变化，群机器人始终可用！
""")
    else:
        print(f"""
🔧 故障排查:
1. 检查webhook URL是否正确
2. 确认机器人未被群管理员禁用
3. 验证网络连接正常
""")

if __name__ == "__main__":
    main()
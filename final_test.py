#!/usr/bin/env python3
"""最终测试：模拟tmux-agent触发通知"""
import time
import os
from dotenv import load_dotenv
from src.tmux_agent.notify import Notifier, NotificationMessage

# 加载环境变量
load_dotenv()

def test_notification_scenarios():
    """测试各种通知场景"""
    notifier = Notifier(channel="wecom_webhook")

    scenarios = [
        {
            "title": "🚀 tmux-agent 启动",
            "body": "**系统状态**: 正常启动\n**监控主机**: local\n**配置文件**: agent-config.yaml\n\n开始监控tmux会话..."
        },
        {
            "title": "⏳ 阶段性进展",
            "body": "**当前阶段**: notify-test\n**进度**: 50%\n**状态**: 执行中\n\n检测到日志匹配: `test-wecom-notification`"
        },
        {
            "title": "✅ 任务完成",
            "body": "**阶段**: notify-test 已成功完成\n**管道**: wecom-test-pipeline\n**耗时**: 2.3秒\n\n🎉 企业微信通知测试完成！"
        }
    ]

    for i, scenario in enumerate(scenarios, 1):
        print(f"📤 发送测试场景 {i}/3: {scenario['title']}")
        try:
            message = NotificationMessage(
                title=scenario["title"],
                body=scenario["body"]
            )
            notifier.send(message)
            print(f"   ✅ 发送成功")
            if i < len(scenarios):
                time.sleep(2)  # 间隔2秒避免刷屏
        except Exception as e:
            print(f"   ❌ 发送失败: {e}")
            return False

    return True

if __name__ == "__main__":
    print("🧪 tmux-agent 通知系统最终测试")
    print("=" * 50)

    success = test_notification_scenarios()

    if success:
        print(f"\n🎉 所有测试通过！")
        print(f"✅ 企业微信群机器人通知系统已就绪")
        print(f"🚀 可以启动 tmux-agent 开始正式使用")
        print(f"\n启动命令:")
        print(f"source .env && .venv/bin/python -m src.tmux_agent.main --config agent-config.yaml")
    else:
        print(f"\n❌ 测试失败，请检查配置")
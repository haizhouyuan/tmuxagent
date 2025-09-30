#!/usr/bin/env python3
"""
企业微信代理客户端
通过固定IP的代理服务器发送消息
"""
import os
import httpx
from dotenv import load_dotenv

def send_via_proxy(title: str, body: str, proxy_url: str = None):
    """通过代理服务器发送消息"""
    load_dotenv()

    proxy_url = proxy_url or os.getenv("WECOM_PROXY_URL")
    if not proxy_url:
        raise RuntimeError("WECOM_PROXY_URL environment variable is required")

    corp_id = os.getenv("WECOM_CORP_ID")
    agent_id = os.getenv("WECOM_AGENT_ID")
    app_secret = os.getenv("WECOM_APP_SECRET")
    touser = os.getenv("WECOM_TOUSER", "@all")

    if not all([corp_id, agent_id, app_secret]):
        raise RuntimeError("Missing required WeChat Work credentials")

    payload = {
        "corp_id": corp_id,
        "agent_id": agent_id,
        "app_secret": app_secret,
        "title": title,
        "body": body,
        "touser": touser
    }

    with httpx.Client(timeout=15.0) as client:
        response = client.post(f"{proxy_url}/send_message", json=payload)
        response.raise_for_status()
        return response.json()

def main():
    """测试代理发送"""
    print("🔄 通过代理服务器发送企业微信消息...")

    try:
        result = send_via_proxy(
            title="🔄 代理服务器测试",
            body="这是通过固定IP代理服务器发送的测试消息\n\n✅ 代理连接成功\n📡 无需担心IP变化问题"
        )
        print("✅ 代理发送成功!")
        print(f"📋 响应: {result}")
    except Exception as e:
        print(f"❌ 代理发送失败: {e}")

if __name__ == "__main__":
    main()
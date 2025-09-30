#!/usr/bin/env python3
"""
企业微信通知烟雾测试脚本
按照 docs/参考1412341.md 的流程，强制直连测试网络链路
"""
import os, sys, json, time
from dotenv import load_dotenv
import httpx

def must_get(name: str) -> str:
    v = os.getenv(name)
    if not v:
        print(f"[FAIL] missing env: {name}", file=sys.stderr)
        sys.exit(2)
    return v

def main():
    print("🔍 企业微信烟雾测试开始...")

    # 加载环境变量
    load_dotenv()

    corp_id = must_get("WECOM_CORP_ID")
    agent_id = must_get("WECOM_AGENT_ID")
    secret = must_get("WECOM_APP_SECRET")
    touser = os.getenv("WECOM_TOUSER", "@all")
    toparty = os.getenv("WECOM_TOPARTY")
    totag = os.getenv("WECOM_TOTAG")

    print(f"📊 配置信息:")
    print(f"   Corp ID: {corp_id}")
    print(f"   Agent ID: {agent_id}")
    print(f"   Target: {touser}")
    print(f"   Secret: {'*' * (len(secret) - 8)}{secret[-8:]}")

    # 检查网络出口IP
    print(f"\n🌐 检查网络出口...")
    try:
        with httpx.Client(trust_env=False, timeout=5.0) as c:
            ip_resp = c.get("https://ipinfo.io/ip")
            exit_ip = ip_resp.text.strip()
            print(f"   直连出口IP: {exit_ip}")
    except Exception as e:
        print(f"   ⚠️ 无法获取出口IP: {e}")
        exit_ip = "unknown"

    # 强制直连，不走代理
    print(f"\n📤 发送测试消息...")
    with httpx.Client(trust_env=False, timeout=10.0) as c:
        try:
            # 1) 获取访问令牌
            print("   获取访问令牌...")
            r = c.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                      params={"corpid": corp_id, "corpsecret": secret})
            r.raise_for_status()
            data = r.json()

            if data.get("errcode") != 0:
                print(f"[FAIL] gettoken: {data}", file=sys.stderr)
                if data.get("errcode") == 60020:
                    print(f"💡 解决方法: 将IP {exit_ip} 添加到企业微信应用的可信IP白名单")
                    print(f"   管理后台: https://work.weixin.qq.com")
                    print(f"   应用管理 → 自建应用({agent_id}) → 开发管理 → 接口IP白名单")
                sys.exit(3)

            token = data["access_token"]
            expires_in = data.get("expires_in", 7200)
            print(f"   ✅ 令牌获取成功，有效期: {expires_in}秒")

            # 2) 发送消息（Markdown 格式）
            print("   发送测试消息...")
            url = "https://qyapi.weixin.qq.com/cgi-bin/message/send"
            test_time = time.strftime("%Y-%m-%d %H:%M:%S")
            payload = {
                "touser": touser,
                "agentid": int(agent_id),
                "msgtype": "markdown",
                "markdown": {
                    "content": f"""**🚀 tmux-agent 烟雾测试**

> **测试时间**: {test_time}
> **出口IP**: {exit_ip}
> **网络状态**: 直连模式

✅ 企业微信通知通道已就绪！

---
*此消息来自 tmux-agent 烟雾测试脚本*"""
                },
                "duplicate_check_interval": 600,
            }

            if toparty: payload["toparty"] = toparty
            if totag: payload["totag"] = totag

            r2 = c.post(url, params={"access_token": token}, json=payload)
            r2.raise_for_status()
            d2 = r2.json()

            if d2.get("errcode") == 0:
                print(f"[SUCCESS] 消息发送成功!")
                print(f"   响应: {json.dumps(d2, ensure_ascii=False)}")
                print(f"\n📱 请检查你的企业微信应用是否收到测试消息")
                sys.exit(0)
            else:
                print(f"[FAIL] send: {d2}", file=sys.stderr)
                if d2.get("errcode") == 60020:
                    print(f"💡 IP {exit_ip} 不在白名单中，需要在企业微信后台添加")
                sys.exit(4)

        except httpx.HTTPError as e:
            print(f"[FAIL] HTTP请求失败: {e}", file=sys.stderr)
            sys.exit(5)
        except Exception as e:
            print(f"[FAIL] 未知错误: {e}", file=sys.stderr)
            sys.exit(6)

if __name__ == "__main__":
    main()
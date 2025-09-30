#!/usr/bin/env python3
"""
企业微信API代理服务器
部署在有固定IP的云主机上，转发企业微信API请求
"""
import os
import time
import json
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import uvicorn

app = FastAPI(title="WeChat Work API Proxy", version="1.0.0")

# 缓存access_token避免频繁请求
token_cache = {"token": None, "expires_at": 0}

class MessageRequest(BaseModel):
    corp_id: str
    agent_id: str
    app_secret: str
    title: str
    body: str
    touser: str = "@all"
    toparty: str = ""
    totag: str = ""

@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "service": "wecom-proxy"}

@app.post("/send_message")
async def send_message(request: MessageRequest):
    """发送企业微信消息"""
    try:
        # 获取access_token
        token = await get_access_token(request.corp_id, request.app_secret)

        # 构建消息payload
        payload = {
            "touser": request.touser,
            "agentid": int(request.agent_id),
            "msgtype": "markdown",
            "markdown": {
                "content": f"**{request.title}**\n\n{request.body}"
            },
            "safe": 0,
        }

        if request.toparty:
            payload["toparty"] = request.toparty
        if request.totag:
            payload["totag"] = request.totag

        # 发送消息
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://qyapi.weixin.qq.com/cgi-bin/message/send",
                params={"access_token": token},
                json=payload
            )
            response.raise_for_status()
            data = response.json()

        if data.get("errcode") != 0:
            raise HTTPException(status_code=400, detail=f"WeChat API error: {data}")

        return {"success": True, "message": "Message sent successfully", "data": data}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def get_access_token(corp_id: str, app_secret: str) -> str:
    """获取并缓存access_token"""
    now = time.time()

    # 检查缓存
    if token_cache["token"] and now < token_cache["expires_at"]:
        return token_cache["token"]

    # 获取新token
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": corp_id, "corpsecret": app_secret}
        )
        response.raise_for_status()
        data = response.json()

    if data.get("errcode") != 0:
        raise HTTPException(status_code=400, detail=f"Token error: {data}")

    # 更新缓存
    token = data["access_token"]
    expires_in = data.get("expires_in", 7200)
    token_cache["token"] = token
    token_cache["expires_at"] = now + expires_in - 60  # 提前1分钟过期

    return token

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
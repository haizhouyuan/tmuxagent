下面给你一套“可直接照做”的工程化方案，把三个问题一次性解决：**模型访问的网络代理与凭证**、**企业微信 Webhook 配置**、**在本地无 root 权限也能跑起来**。我按“最小改动、最快自检”的思路写，所有命令都默认在 **Ubuntu 22.04 / bash / Python 3.11** 环境下执行；tmux/orchestrator 会继承你启动它的那一层 shell 环境，所以确保在同一个终端里 `source` 完再启动即可。

---

# 一、模型访问：网络代理与凭证（适配 Clash/Clash Verge、proxychains4 与 tmux）

> 目标：让 `codex` 和 orchestrator 在**有代理**、**有凭证**的前提下稳定访问模型 API。

### 1) 建 `.env`（统一交给 tmux/orchestrator 继承）

把以下内容保存到你的工程根目录或家目录（两选一）：

```bash
# ~/.env 或者 <你的项目>/.env
# —— 代理（按你的 Clash/Verge 端口改；HTTP 和 SOCKS5 任意其一即可，也可都设）
HTTP_PROXY=http://127.0.0.1:7890
HTTPS_PROXY=http://127.0.0.1:7890
ALL_PROXY=socks5://127.0.0.1:7890
NO_PROXY=localhost,127.0.0.1,::1,.local,.lan

# —— OpenAI（或你实际使用的提供方）API 凭证
# Codex CLI 常用的环境变量名是 OPENAI_API_KEY（若你切到别家，换成该家的KEY变量名即可）
OPENAI_API_KEY=sk-xxxx_your_key_here

# —— 可选：Codex 默认模型与超时（按你日常使用习惯）
CODEX_MODEL=gpt-5-codex
CODEX_TIMEOUT_SECONDS=120
```

> 如果你用 **HTTP** 代理，`ALL_PROXY` 可以不设；如果用 **SOCKS5**，建议同时设 `ALL_PROXY`。
> `NO_PROXY` 一定要包含 `localhost,127.0.0.1,::1`，避免本地回环调用也走代理。

### 2) 启用环境（当前终端 + tmux 环境）

```bash
# 当前 shell
set -a && source ~/.env && set +a

# 确保 tmux 也拿到最新环境（tmux server 早于你设置时会“粘住”旧环境）
tmux kill-server 2>/dev/null || true
```

> 之后再启动 orchestrator / tmux 会话，它们就能继承这些变量。

### 3) （可选）proxychains4 兜底配置

如果你要强制所有子进程都走代理（尤其是第三方 CLI 忽略环境变量时），配 proxychains4：

```bash
sudo cp /etc/proxychains4.conf /etc/proxychains4.conf.bak
sudo sed -i 's/^# proxy_dns/proxy_dns/' /etc/proxychains4.conf
sudo sed -i 's/^socks4.*/socks5  127.0.0.1 7890/' /etc/proxychains4.conf
# 测试
proxychains4 curl -s https://ifconfig.me
```

之后把 orchestrator 的启动命令或 `codex` 命令前加 `proxychains4` 即可（不建议长期使用，除非你明确要强制代理）。

### 4) 连通性自检（必须做）

```bash
# 端口通不通
nc -vz 127.0.0.1 7890

# API 通不通（2xx/3xx 即可；401/403 多半是KEY或权限问题）
curl -I https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"

# codex 最小调用（替换为你真实可用的标志参数；若无就先看 --help）
codex --help | head -n 20
codex --model "${CODEX_MODEL:-gpt-5-codex}" "ping" || true
```

---

# 二、企业微信 Webhook（微信群「自定义机器人」方案，免 IP 白名单）

> 目标：拿到一个 **Webhook URL**，并通过环境变量 **WECOM_WEBHOOK** 提供给 orchestrator。

### 1) 在企业微信里创建机器人

1. 打开你的**企业微信群** → 右上角 **群设置** → **群机器人** → **添加机器人** → 选择 **自定义机器人**。
2. 完成后会得到一个形如
   `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
   的 **Webhook URL**。

> 机器人方案**无需**企业内网 IP 白名单；如果你用“企业应用”方式才需要白名单（不推荐做告警渠道的第一优先）。

### 2) 配置环境变量（注意变量名）

你的报告里报错是 `WECOM_WEBHOOK not set`，所以请统一用这个变量名（之前你也用过 `WECOM_WEBHOOK_URL`，会不一致）：

```bash
# 仍然写进 ~/.env 或项目 .env
WECOM_WEBHOOK="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=替换成你的key"
```

然后 `set -a && source ~/.env && set +a` 让它生效。

### 3) 发送一条测试消息（两种方式均可）

**curl 版：**

```bash
curl -sS -X POST "$WECOM_WEBHOOK" \
  -H 'Content-Type: application/json' \
  -d '{"msgtype":"markdown","markdown":{"content":"✅ *Orchestrator* Webhook 连接正常\n> 服务器: Ubuntu 22.04\n> 时间: '"$(date '+%F %T')"' "}}'
```

**Python 版：**（保存为 `test_wecom.py`）

```python
import os, json, requests, datetime
hook = os.environ.get("WECOM_WEBHOOK")
assert hook, "WECOM_WEBHOOK not set"
payload = {
  "msgtype": "markdown",
  "markdown": {
    "content": f"✅ **Orchestrator** Webhook OK\n> 时间: {datetime.datetime.now():%F %T}"
  }
}
r = requests.post(hook, headers={"Content-Type":"application/json"}, data=json.dumps(payload), timeout=10)
r.raise_for_status()
print("OK", r.text)
```

```bash
python3 test_wecom.py
```

> **常见坑：**
>
> * Webhook URL 前后多了空格/引号（尤其是复制到 `.env` 后），会导致 400。
> * 机器人被移除/换群后，旧 Webhook 会失效。
> * Markdown 里如果放 `<` `&` 等字符，建议转义或改成代码块。

---

# 三、Codex CLI 安装与 PATH、以及“本地无权限”的三种解决姿势

> 目标：即使没有 root 权限，也能把 `codex` 放到 PATH 里让 orchestrator 找到；找不到时可先用 FakeCodex 跑通闭环。

### 方案 A｜用户级安装（**推荐**，无需 root）

把官方或你已有的 `codex` 可执行文件放到 `~/.local/bin`，并确保 PATH：

```bash
mkdir -p ~/.local/bin
# 例如：tar -xzf codex_linux_amd64.tar.gz -C ~/.local/bin
# 或 cp codex ~/.local/bin/codex && chmod +x ~/.local/bin/codex

# 确保 PATH 覆盖
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

which codex && codex --version
```

### 方案 B｜项目自带 bin + 启动时临时 PATH

不动 bashrc，只在启动 orchestrator 时提供 PATH：

```bash
mkdir -p .tmuxagent/bin
# cp codex .tmuxagent/bin/codex && chmod +x .tmuxagent/bin/codex
PATH="$(pwd)/.tmuxagent/bin:$PATH" codex --version
# 你的启动脚本里同样先拼 PATH 再 exec orchestrator
```

### 方案 C｜FakeCodex（**无权限/无网络时先打通链路**）

> 这是你在报告里提到的“先验证 orchestrator 自身闭环”的办法。下面这个假实现仅回显并产出 `__TMUXAGENT_RESULT ... 0` 的成功标记。

保存为 `.tmuxagent/bin/codex`：

```bash
#!/usr/bin/env bash
# 最小可用的“假 codex”，用于流程打通；真实集成后替换为真 codex
prompt="$*"
echo "FakeCodex: received -> $prompt"
# 模拟一点推理延迟
sleep 1
# 关键：回传 orchestrator 能识别的成功标记（命令ID可由 orchestrator 插装注入）
echo "__TMUXAGENT_RESULT fake-run-$(date +%s) 0"
exit 0
```

```bash
chmod +x .tmuxagent/bin/codex
PATH="$(pwd)/.tmuxagent/bin:$PATH" which codex
```

> 有了它，**A 场景**和大多数“命令注入→回传→审计”都会通；等你把真 codex 放进来，替换这个脚本即可。

---

# 四、把环境变量可靠地“喂给” orchestrator 与 tmux

### 1) 直接在启动脚本里 `source`

```bash
# run_orchestrator.sh
#!/usr/bin/env bash
set -euo pipefail
set -a && source ~/.env && set +a
export PATH="$(pwd)/.tmuxagent/bin:$HOME/.local/bin:$PATH"

# （可选）强制通过 proxychains4 启动：
# exec proxychains4 python3 -m src.tmux_agent.main --config .tmuxagent/orchestrator.toml
exec python3 -m src.tmux_agent.main --config .tmuxagent/orchestrator.toml
```

```bash
chmod +x run_orchestrator.sh
./run_orchestrator.sh
```

### 2) 若你用 systemd **用户服务**（无 root 权限）

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/orchestrator.service <<'EOF'
[Unit]
Description=Tmux Orchestrator (User)

[Service]
Type=simple
EnvironmentFile=%h/.env
# 加 PATH，确保可见 codex
Environment=PATH=%h/.local/bin:%h/.tmuxagent/bin:/usr/bin:/bin
WorkingDirectory=%h/projects/你的项目根目录
ExecStart=/usr/bin/python3 -m src.tmux_agent.main --config .tmuxagent/orchestrator.toml
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now orchestrator
systemctl --user status orchestrator
```

> 用户服务不需要 root；如果 `.env` 更新，`systemctl --user restart orchestrator` 即可。

---

# 五、Prometheus `/metrics` 验证与常见问题排查

### 1) 快速自检

```bash
# 先确保 orchestrator 没被 codex 异常直接干掉
ss -lntp | grep 9108 || true
curl -sS http://localhost:9108/metrics | head -n 20
```

### 2) 若端口没起来

* 看 **orchestrator 日志**（你报告里路径像 `.tmuxagent/logs/orchestrator-actions.jsonl`，同时也看标准输出/错误）。
* 多半是 `FileNotFoundError: codex` 或 `WECOM_WEBHOOK not set` 导致**进程直接退出**。
* 按本文前两节修完，**先用 FakeCodex** 验证 `/metrics` 能起来，再替换真 codex。

---

# 六、一次性自检脚本（复制即用）

保存为 `diagnose_orchestrator.sh`：

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "== 1. 载入环境 =="
set -a && source ~/.env && set +a
echo "HTTP_PROXY=${HTTP_PROXY:-}"
echo "ALL_PROXY=${ALL_PROXY:-}"
echo "OPENAI_API_KEY=${OPENAI_API_KEY:+<set>}"; echo

echo "== 2. 代理连通性 =="
nc -vz 127.0.0.1 7890 || true
curl -I https://api.openai.com/v1/models -H "Authorization: Bearer ${OPENAI_API_KEY:-}" || true
echo

echo "== 3. codex 可执行 =="
command -v codex || { echo "!! codex 不在 PATH"; exit 1; }
codex --help | head -n 5 || true
echo

echo "== 4. WeCom Webhook 测试 =="
test -n "${WECOM_WEBHOOK:-}" || { echo "!! WECOM_WEBHOOK 未设置"; exit 1; }
curl -sS -X POST "$WECOM_WEBHOOK" -H 'Content-Type: application/json' \
  -d '{"msgtype":"text","text":{"content":"[自检] Webhook OK"}}' | tee /dev/stderr
echo

echo "== 5. 启动 orchestrator（前台 5 秒探活后退出）=="
python3 -m src.tmux_agent.main --config .tmuxagent/orchestrator.toml &
PID=$!
sleep 5 || true
curl -sS http://localhost:9108/metrics | head -n 10 || true
kill $PID || true
echo "== DONE =="
```

```bash
chmod +x diagnose_orchestrator.sh
./diagnose_orchestrator.sh
```

---

# 七、常见坑与对策（精炼版）

* **环境“漂移”**：tmux server 启得早，拿到旧环境。
  ✅ 解决：改完 `.env` 后先 `tmux kill-server`，或 `tmux set-environment -g KEY VAL` 显式刷新。

* **Webhook 变量名不一致**：`WECOM_WEBHOOK_URL` vs `WECOM_WEBHOOK`。
  ✅ 解决：统一改成报错里使用的 `WECOM_WEBHOOK`，并确保 `.env` 被加载。

* **codex 不在 PATH** / 无 root：
  ✅ 解决：`~/.local/bin` 或项目 `.tmuxagent/bin` + 修改 PATH；临时用 **FakeCodex** 打通闭环。

* **代理只对 shell 生效，不对服务生效**：
  ✅ 解决：用 `systemd --user` 的 `EnvironmentFile` 或在启动脚本里 `source ~/.env`，确保子进程继承。

* **端口被占用**（9108/8703）：
  ✅ 解决：`ss -lntp | grep 9108` 找占用进程，改端口或停掉旧实例。

---

## 你刚才问的三句具体回答

1. **“需要我如何准备访问模型所需的网络代理与凭证，我是想通过 ？”**

   * 按上文“一、模型访问”操作：用 **Clash/Clash Verge 本机代理**（常见 7890 端口），在 `.env` 里设置 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`，再把 **`OPENAI_API_KEY`**（或你实际使用的提供方 KEY）写入 `.env`。最后 `set -a && source ~/.env && set +a`，并重启 tmux/orchestrator。

2. **“我应当如何设定 wecom webhook”**

   * 在企业微信群里添加**自定义机器人**拿到 URL → 写入 `.env` 的 **`WECOM_WEBHOOK`** → 用上面的 `curl` 或 `test_wecom.py` 发一条测试消息确认连通。

3. **“还有如何解决本地无权限的问题”**

   * 用 **用户级安装**方式：把 `codex` 放 `~/.local/bin`，或放在项目的 `.tmuxagent/bin` 并在启动时临时扩展 `PATH`；没有真 codex 时先用 **FakeCodex** 跑通 orchestrator 的全链路与 `/metrics` 监控。需要常驻运行就用 **systemd 用户服务**（`~/.config/systemd/user`），完全不需要 root。

---

如果你愿意，我也可以把 **FakeCodex 脚本**、**run_orchestrator.sh**、**systemd 用户服务 unit**、**wecom 测试脚本**四件套按你的项目目录结构直接生成成品给你拷贝粘贴。


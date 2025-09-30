# Codex 双通道（官方 + GAC）配置与使用说明

> 目标：在**同一台机器**上同时使用 OpenAI 官方 Codex CLI 与 GAC Codex（网关版），**互不污染、可随时切换**，并为自动化（orchestrator/CI）提供**稳定可执行入口**。本文给到**一步到位命令**、**验收方法**与**排错手册**。

---

## 0. 架构与约定

- 官方 CLI（Pro 账号）固定走 **Node v22**，配置放在：`~/.home-codex-official/.codex/`
    
- GAC CLI（API Key）固定走 **Node v20**，配置放在：`~/.home-codex-gac/.codex/`
    
- 交互终端里：
    
    - `codex` → **官方**（通过 alias 绑定到官方包装函数）
        
    - `codex-gac` → **GAC**（函数与同名可执行包装器二选一；二者等价）
        
- 自动化（orchestrator/脚本/CI）：
    
    - 用**可执行包装器**：`~/.local/bin/codex-official` 或 `~/.local/bin/codex-gac`
        
- 代理策略：
    
    - GAC 使用 **环境变量代理**（`ALL_PROXY=socks5h://127.0.0.1:7890`），**仅对命令进程生效**（不污染父 shell）
        

---

## 1. 先决条件

```bash
# 安装/激活 nvm（若已安装可跳过）
export NVM_DIR="$HOME/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"

# 安装 Node 版本
nvm install 22
nvm install 20

# 让新开终端默认使用 v22（官方用）
nvm alias default 22
grep -q 'nvm use --silent default' ~/.bashrc || \
  sed -i '/nvm\.sh/ a\nvm use --silent default >/dev/null 2>&1' ~/.bashrc
```

> **说明**：我们将官方 Codex 固定在 v22，GAC 固定在 v20，以避免全局 npm “抢位”。

---

## 2. 准备 GAC Key（仅 GAC 通道需要）

将 GAC 提供的 **CODEX API Key** 写入本地文件（**权限 600**）：

```bash
cat > ~/.gac.env <<'EOF'
export CODEX_API_KEY='在此粘贴你的_GAC_Codex_API_KEY_字符串'
EOF
chmod 600 ~/.gac.env
```

> **不要**把 Key 直接写入 `~/.bashrc`；不要提交到任何仓库。

---

## 3. 写入函数（交互终端使用）

把下面函数追加到 `~/.bashrc`，然后 `source ~/.bashrc`：

```bash
# 官方 codex：Node v22 + 独立 HOME + 清理第三方变量
codex-official() {
  export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
  nvm use 22 >/dev/null 2>&1
  # 清理可能的第三方/代理变量，避免污染
  unset CODEX_API_KEY CODEX_BASE_URL CODEX_SERVER OPENAI_BASE_URL ANTHROPIC_BASE_URL \
        HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy no_proxy
  local BIN22="$(dirname "$(nvm which 22)")/codex"
  HOME="$HOME/.home-codex-official" "$BIN22" "$@"
}

# GAC codex：Node v20 + KEY + 环境变量代理（仅对子进程生效）+ 独立 HOME
codex-gac() {
  export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
  nvm use 20 >/dev/null 2>&1
  [ -f "$HOME/.gac.env" ] && . "$HOME/.gac.env"
  local BIN20="$(dirname "$(nvm which 20)")/codex"
  HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= NO_PROXY= no_proxy= \
  ALL_PROXY="socks5h://127.0.0.1:7890" \
  HOME="$HOME/.home-codex-gac" \
  "$BIN20" "$@"
}

# 让“裸命令” codex 永远走官方隔离 HOME（交互终端友好）
grep -q "alias codex='codex-official'" ~/.bashrc || echo "alias codex='codex-official'" >> ~/.bashrc
```

使其生效：

```bash
source ~/.bashrc
```

---

## 4. 创建**可执行包装器**（用于 orchestrator/脚本/CI）

> 脚本/服务调用**不会**继承你的 alias，因此需要**真实可执行文件**。

```bash
mkdir -p ~/.local/bin

# 官方包装器
cat > ~/.local/bin/codex-official <<'SH'
#!/usr/bin/env bash
set -euo pipefail
export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm use 22 >/dev/null 2>&1
unset CODEX_API_KEY CODEX_BASE_URL CODEX_SERVER OPENAI_BASE_URL ANTHROPIC_BASE_URL \
      HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy no_proxy
BIN22="$(dirname "$(nvm which 22)")/codex"
HOME="$HOME/.home-codex-official" "$BIN22" "$@"
SH
chmod +x ~/.local/bin/codex-official

# GAC 包装器
cat > ~/.local/bin/codex-gac <<'SH'
#!/usr/bin/env bash
set -euo pipefail
export NVM_DIR="$HOME/.nvm"; [ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm use 20 >/dev/null 2>&1
[ -f "$HOME/.gac.env" ] && . "$HOME/.gac.env"
BIN20="$(dirname "$(nvm which 20)")/codex"
HTTP_PROXY= HTTPS_PROXY= http_proxy= https_proxy= NO_PROXY= no_proxy= \
ALL_PROXY="socks5h://127.0.0.1:7890" \
HOME="$HOME/.home-codex-gac" \
"$BIN20" "$@"
SH
chmod +x ~/.local/bin/codex-gac

# 确保 ~/.local/bin 在 PATH 前列
grep -q 'export PATH="$HOME/.local/bin:$PATH"' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

---

## 5. 验收与自检

### 5.1 基本验证（交互终端）

```bash
# 版本与命中路径
type -a codex
which codex && codex --version

# 官方通道
codex "/status"
printf "hello\n" | codex

# GAC 通道
codex-gac "/status"
printf "hello\n" | codex-gac
```

### 5.2 配置隔离验证（防污染）

```bash
# 官方配置不应含任何 gaccode/base_url/server 覆盖项
grep -nEi 'gaccode|base_url|server' ~/.home-codex-official/.codex/config.toml || echo "<official clean>"

# GAC 配置通常会含 base_url 指向 GAC（若无也可能是 CLI 隐式）
grep -nEi 'gaccode|base_url|server' ~/.home-codex-gac/.codex/config.toml || echo "<gac implicit or clean>"

# （可选）直接备份旧的默认目录，避免第三方误用
[ -d ~/.codex ] && mv ~/.codex ~/.codex.backup.$(date +%F-%H%M%S)
```

### 5.3 一键体检（可选，加入 `~/.bashrc`）

```bash
codex-sanity() {
  echo "== 官方（隔离） =="; printf "/status\n" | codex-official | sed -n '1,30p'
  echo "== GAC（隔离） =="; printf "/status\n" | codex-gac       | sed -n '1,30p'
  echo "== 配置指向 =="
  echo "[official]"; grep -nEi 'gaccode|base_url|server' ~/.home-codex-official/.codex/config.toml || echo "<clean>"
  echo "[gac]     "; grep -nEi 'gaccode|base_url|server' ~/.home-codex-gac/.codex/config.toml       || echo "<none or implicit>"
}
```

---

## 6. 在 orchestrator/CI 中使用

### 6.1 走 GAC（推荐，已验证稳定）

`.tmuxagent/orchestrator.toml`（示例）：

```toml
[codex]
bin = "/home/yuanhaizhou/.local/bin/codex-gac"
# 根据策略选择是否开启全自动
# extra_args = ["--dangerously-bypass-approvals-and-sandbox"]
```

### 6.2 走官方

先**登录一次**到官方隔离 HOME（只需一次）：

```bash
codex-official login
codex-official "/status"
```

orchestrator 指向官方包装器：

```toml
[codex]
bin = "/home/yuanhaizhou/.local/bin/codex-official"
```

> **注意**：不要在 orchestrator 中依赖 alias；始终用**绝对可执行路径**。

---

## 7. 常见问题排查

### 7.1 官方也去请求 `https://gaccode.com/.../responses`

- **原因**：官方配置被污染（`~/.codex/config.toml` 或当前 HOME 下的同名文件含 `base_url` 指向 GAC）。
    
- **处理**：
    
    - 方案 B（推荐）：永远用 `codex-official`（官方隔离 HOME），并把“裸命令 codex” alias 到它。
        
    - 或清污：备份后清除 `base_url`、注释掉 `[mcp_servers.playwright]` 区块。
        

### 7.2 `proxychains4 codex-gac` 提示找不到进程

- 原因：`codex-gac` 是**函数**不是可执行；请使用我们创建的**包装器** `/home/…/.local/bin/codex-gac`。
    

### 7.3 `No prompt provided`

- 官方 CLI 的一次性模式需要参数或 stdin：
    
    ```bash
    codex "/status"
    printf "hello\n" | codex
    ```
    

### 7.4 TTY 报错（os error 6）

- 在非交互环境直接跑 `codex /status` 可能报 TTY；用管道：
    
    ```bash
    printf "/status\n" | codex-official
    ```
    

### 7.5 Stream error / 401（GAC）

- 优先确认 **Key 模式**、**环境变量代理** 与 **DNS 走 socks5h**：
    
    - `~/.gac.env` 中有 `CODEX_API_KEY`（有效）
        
    - 使用 `ALL_PROXY="socks5h://127.0.0.1:7890"`（注意 `socks5h`，含远端 DNS）
        
    - 确保 `codex-gac` 中**只对本次命令**注入代理（不 export）
        

### 7.6 版本混乱（命中到 v20 的 codex）

- 已通过：
    
    - `nvm alias default 22`
        
    - 清退 `~/.local/bin/codex` 与 `~/local/node/bin/codex` 的“影子”
        
    - 包装器内用 `nvm which` 计算**绝对二进制路径**
        
- 若仍异常：`type -a codex` 检查命中顺序。
    

---

## 8. 安全与规范

- Key 仅存放于 `~/.gac.env`，权限 `600`；**绝不**提交。
    
- 不在 `~/.bashrc` 全局 `export ALL_PROXY`；只在命令行临时注入。
    
- 生产仓库/敏感代码建议优先使用**官方通道**；GAC 作为额度兜底。
    
- 若误泄密钥：立刻在平台**吊销/重置**，并清理 shell 历史。
    

---

## 9. 升级与回滚

- 官方 Codex（npm 全局）：
    
    ```bash
    nvm use 22
    npm -g i @openai/codex   # 以官方发布名为准
    ```
    
- GAC Codex（若需要 npm 升级）：
    
    ```bash
    nvm use 20
    npm -g i https://gaccode.com/codex/install
    ```
    
- 回滚：恢复对应备份或重新安装；包装器无需变更。
    

---

## 10. 快速“复制即用”清单

```bash
# —— 必做：函数、包装器、Key、代理策略、隔离 HOME —— #
# 1) nvm + Node v22/v20，设置默认 v22
# 2) ~/.gac.env 写入 CODEX_API_KEY（600 权限）
# 3) ~/.bashrc 写入 codex-official/codex-gac 函数 + alias codex
# 4) ~/.local/bin 写入 codex-official/codex-gac 包装器并 chmod +x
# 5) source ~/.bashrc；type -a codex；codex --version
# 6) 验证：
printf "/status\n" | codex-official | sed -n '1,30p'
printf "/status\n" | codex-gac       | sed -n '1,30p'
```

完成以上步骤后，**大家只需记住两条命令**：

- 交互用：`codex`（官方） / `codex-gac`（GAC）
    
- 自动化用：`~/.local/bin/codex-official` / `~/.local/bin/codex-gac`
    

> 如果你需要，我可以把本说明做成 `README_Codex_Setup.md` 并附上自动化脚本（`setup_codex_dual.sh`），一键完成配置与校验。
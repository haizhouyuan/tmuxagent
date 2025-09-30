#!/bin/bash
# Codex CLI 网络诊断脚本

echo "=== Codex CLI 网络诊断 ==="
echo "当前时间：$(date)"
echo

echo "1. 检查代理服务状态："
netstat -tlnp | grep 7890 || echo "   ❌ 端口 7890 未监听"

echo
echo "2. 检查 proxychains 配置："
echo "   ProxyList: $(tail -1 /etc/proxychains4.conf)"
echo "   DNS: $(grep -o 'proxy_dns' /etc/proxychains4.conf || echo '未启用')"

echo
echo "3. 测试基础网络连接："
. ~/.proxy-on.sh >/dev/null 2>&1
timeout 5 proxychains4 -q curl -s -I https://api.openai.com/v1/models 2>/dev/null | head -2 || echo "   ❌ proxychains + curl 失败"

echo
echo "4. 环境变量检查："
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
export ALL_PROXY=socks5://127.0.0.1:7890
export OPENAI_API_KEY=sk-placeholder-key-for-testing
echo "   HTTP_PROXY: $HTTP_PROXY"
echo "   HTTPS_PROXY: $HTTPS_PROXY"
echo "   ALL_PROXY: $ALL_PROXY"
echo "   OPENAI_API_KEY: ${OPENAI_API_KEY:0:10}..."

echo
echo "5. Codex CLI 测试："
echo "   直接调用（10s 超时）："
timeout 10 /home/yuanhaizhou/.nvm/versions/node/v22.19.0/bin/codex --dangerously-bypass-approvals-and-sandbox "test direct" 2>&1 | head -3 || echo "   ❌ 直接调用失败"

echo
echo "   通过 proxychains（10s 超时）："
timeout 10 proxychains4 -q /home/yuanhaizhou/.nvm/versions/node/v22.19.0/bin/codex --dangerously-bypass-approvals-and-sandbox "test proxy" 2>&1 | head -3 || echo "   ❌ proxychains 调用失败"

echo
echo "6. Node.js 网络库测试："
node -e "
const https = require('https');
const options = {
  hostname: 'api.openai.com',
  port: 443,
  path: '/v1/models',
  method: 'GET',
  headers: { 'Authorization': 'Bearer $OPENAI_API_KEY' }
};

const req = https.request(options, (res) => {
  console.log('   ✅ Node.js HTTPS 请求成功:', res.statusCode);
});

req.on('error', (err) => {
  console.log('   ❌ Node.js HTTPS 请求失败:', err.code);
});

req.setTimeout(5000, () => {
  console.log('   ❌ Node.js HTTPS 请求超时');
  req.destroy();
});

req.end();
" 2>/dev/null || echo "   ❌ Node.js 测试失败"

echo
echo "=== 诊断完成 ==="
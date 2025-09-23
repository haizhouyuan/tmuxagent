# tmux-sentry

> 🤖 Intelligent tmux session monitoring and automation agent

tmux-sentry is an advanced monitoring and automation system for tmux sessions, designed to intelligently track AI agent workflows, automatically execute pipelines, and provide human-in-the-loop approval capabilities.

## 🚀 Key Features

### 🧠 Intelligent Log Parsing
- **Multi-strategy parsing** with automatic fallback
- **SENTRY format detection** for AI agent output
- **Heuristic analysis** for unstructured logs
- **High confidence scoring** for reliable decisions

### 🔐 Security & Reliability  
- **SQLite state persistence** - survives system restarts
- **Encrypted configuration** support
- **Secure approval tokens** with HMAC signing
- **Automatic backup** and recovery

### 🔔 Multi-Channel Notifications
- **Console output** for development
- **WeChat Work (企业微信)** for team collaboration  
- **Server酱** for mobile notifications
- **Email & Slack** support
- **Extensible plugin architecture**

### ✋ Human-in-the-Loop Approvals
- **File-based approvals** for simple workflows
- **Web callback approvals** for mobile access
- **Secure token-based authentication**
- **Configurable approval timeouts**

### 🔄 External Service Integration
- **GitHub Actions** status monitoring
- **Docker build** progress tracking
- **Custom webhook** support
- **Automatic retry** and timeout handling

## 📋 Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    tmux-sentry Engine                       │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │   Parser    │ │   State     │ │  Approval   │           │
│  │   System    │ │  Manager    │ │   System    │           │
│  └─────────────┘ └─────────────┘ └─────────────┘           │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐           │
│  │ Notification│ │  External   │ │    tmux     │           │
│  │   Manager   │ │  Checker    │ │   Client    │           │
│  └─────────────┘ └─────────────┘ └─────────────┘           │
└─────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
              ┌─────▼──────┐    ┌──────▼──────┐
              │    SSH     │    │   SQLite    │
              │Connection  │    │  Database   │
              └────────────┘    └─────────────┘
```

## 🚧 Critical Risk Mitigations

This project addresses the key risks identified in AI-driven tmux automation:

### ❌ Problem: AI Output Format Instability  
✅ **Solution**: Smart wrapper script with automatic SENTRY format injection

### ❌ Problem: State Loss on NAS Restart
✅ **Solution**: SQLite persistence with automatic recovery

### ❌ Problem: False Positives from Async Operations  
✅ **Solution**: Real-time external status checking (GitHub API, webhooks)

### ❌ Problem: Security Vulnerabilities
✅ **Solution**: Encrypted configuration, secure tokens, proper credential management

## 🛠 Quick Start

### 1. Installation

```bash
# Clone the repository
git clone https://github.com/your-org/tmux-sentry.git
cd tmux-sentry

# Install dependencies
pip install -r requirements.txt

# Or install in development mode
pip install -e .
```

### 2. Configuration

```bash
# Initialize configuration templates
tmux-sentry init

# Copy and edit the host configuration
cp configs/hosts.example.yaml hosts.yaml
# Edit hosts.yaml with your SSH and tmux settings

# Create environment file for sensitive data
cat > .env << EOF
APPROVAL_SECRET=your-secure-secret-here
WECHAT_WORK_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY
GITHUB_TOKEN=ghp_your_github_token
NOTIFICATION_CHANNELS=stdout,wechat_work
EOF
```

### 3. AI Agent Setup

Configure your AI agents (codex, claude, etc.) to output SENTRY format:

```bash
# Use the smart wrapper for automatic format injection
python scripts/smart_wrapper.py codex chat --project myapp

# Or add to AI system prompt:
# "When completing stages, output: ### SENTRY {\"type\":\"STATUS\",\"stage\":\"build\",\"ok\":true}"
```

### 4. Start Monitoring

```bash
# Check connection status
tmux-sentry status --hosts hosts.yaml

# Start monitoring (press Ctrl+C to stop)
tmux-sentry run --hosts hosts.yaml --policy policies/default.yaml --env-file .env
```

## 📖 Usage Examples

### Example 1: Basic CI/CD Pipeline

```yaml
# policies/webapp.yaml
pipelines:
  - name: "webapp-ci"
    match:
      window_name: "^agent:codex-ci$"
    stages:
      - name: "lint"
        actions_on_start:
          - send_keys: "npm run lint"
        success_when:
          - log_regex: "(?i)0 problems"
        
      - name: "test"
        triggers:
          - after_stage_success: "lint"
        actions_on_start:
          - send_keys: "npm test"
          
      - name: "build"
        triggers:
          - after_stage_success: "test"
        require_approval: true  # Human approval required
        actions_on_start:
          - send_keys: "npm run build"
```

### Example 2: GitHub Actions Integration

```yaml
- name: "deploy"
  actions_on_start:
    - send_keys: "gh workflow run deploy.yml"
    - external_check:
        type: "github_action" 
        repository: "user/repo"
        max_wait: 600
  success_when:
    - external_status: "success"
```

### Example 3: Approval Workflow

```bash
# When approval is needed, you'll get a notification with:
# File path: ~/.tmux_sentry/approvals/pane1__build__1234567890.txt

# Approve via file
echo "approve" > ~/.tmux_sentry/approvals/pane1__build__1234567890.txt

# Or via web (if web server is running)
# Click the approval link sent to WeChat Work
```

## 🧪 Testing

```bash
# Run unit tests
pytest tests/test_parser.py -v

# Run integration tests  
pytest tests/test_integration.py -v

# Run all tests with coverage
pytest --cov=tmux_sentry --cov-report=html
```

## 📊 Monitoring & Observability

### State Database

```bash
# Check database statistics
sqlite3 ~/.tmux_sentry/state.db "SELECT COUNT(*) FROM pane_states;"

# View recent stage history  
sqlite3 ~/.tmux_sentry/state.db "SELECT * FROM stage_history ORDER BY started_at DESC LIMIT 10;"
```

### Logs

```bash
# Run with debug logging
tmux-sentry run --log-level DEBUG --hosts hosts.yaml --policy policies/default.yaml

# Check approval requests
ls -la ~/.tmux_sentry/approvals/
```

## 🔧 Advanced Configuration

### Environment Variables

```bash
# Core settings
APPROVAL_SECRET=your-secret           # Approval token security  
DATABASE_PATH=~/.tmux_sentry/state.db # SQLite database location
LOG_LEVEL=INFO                        # Logging verbosity

# Notification settings
NOTIFICATION_CHANNELS=stdout,wechat_work,email
WECHAT_WORK_WEBHOOK=https://...
SERVERCHAN_API_KEY=SCT...
SMTP_SERVER=smtp.gmail.com
SMTP_USER=your-email@gmail.com

# External service integration
GITHUB_TOKEN=ghp_...                  # For GitHub Actions monitoring
```

### Security Best Practices

```bash
# Encrypt sensitive configuration values
export ENCRYPTION_KEY_FILE=~/.tmux_sentry/master.key

# Use proper file permissions
chmod 600 .env hosts.yaml
chmod 700 ~/.tmux_sentry/

# Rotate approval secrets regularly
# Set short token TTL for approval tokens
APPROVAL_TOKEN_TTL=3600  # 1 hour
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes and add tests
4. Run the test suite: `pytest`
5. Commit your changes: `git commit -m 'Add amazing feature'`
6. Push to the branch: `git push origin feature/amazing-feature`  
7. Open a Pull Request

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Inspired by the need for reliable AI agent monitoring
- Built with Python, SQLite, and the excellent `paramiko` SSH library
- Uses `rich` for beautiful console output
- Special thanks to the tmux community for the powerful multiplexer

## 📞 Support

- 📖 [Documentation](docs/)
- 🐛 [Issue Tracker](https://github.com/your-org/tmux-sentry/issues)
- 💬 [Discussions](https://github.com/your-org/tmux-sentry/discussions)

---

**tmux-sentry**: Making AI agent workflows reliable, observable, and human-controlled. 🚀
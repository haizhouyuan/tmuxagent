# 通知集成指南

## 代码集成点

### 1. 在Policy执行器中添加通知
```python
# src/tmux_agent/policy.py 或相关模块
def execute_stage(self, stage, context):
    if stage.get("on_start_notify"):
        self.send_notification(stage["on_start_notify"], context)

    try:
        result = self.run_stage_actions(stage)
        if result.success and stage.get("on_success_notify"):
            self.send_notification(stage["on_success_notify"], context)
    except Exception as e:
        if stage.get("on_fail_notify"):
            context["error_message"] = str(e)
            self.send_notification(stage["on_fail_notify"], context)
```

### 2. 在Orchestrator中添加里程碑通知
```python
# 会话开始
self.notify_milestone("session_start", {
    "session_id": self.session_id,
    "project_name": self.project.name,
    "timestamp": datetime.now().isoformat()
})

# 任务分析完成
self.notify_milestone("analysis_complete", {
    "task_count": len(self.tasks),
    "complexity_level": self.assess_complexity()
})

# 需要干预
self.notify_milestone("intervention_needed", {
    "intervention_reason": reason,
    "urgency_level": "high"
})
```

### 3. 配置通知节流
```python
class NotificationThrottler:
    def __init__(self, min_interval=60):  # 最小间隔60秒
        self.last_sent = {}
        self.min_interval = min_interval

    def should_send(self, notification_key):
        now = time.time()
        last = self.last_sent.get(notification_key, 0)
        if now - last >= self.min_interval:
            self.last_sent[notification_key] = now
            return True
        return False
```

## 配置示例

### 环境变量
```bash
# 不同紧急程度的通知目标
WECOM_TOUSER_NORMAL=@all          # 常规通知发给所有人
WECOM_TOUSER_URGENT=admin|lead    # 紧急通知仅发给管理员
WECOM_TOUSER_DEBUG=developer      # 调试信息仅发给开发者
```

### Policy配置增强
```yaml
stages:
  - name: "critical-deploy"
    require_approval: true
    notification_level: "urgent"  # 紧急级别
    on_start_notify:
      title: "🚨 关键部署待审批"
      body: "生产环境部署需要立即审批"
      target: "urgent"  # 使用紧急通知目标
```

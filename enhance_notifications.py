#!/usr/bin/env python3
"""
增强orchestrator和policy模板的通知点
根据 docs/参考1412341.md 的建议添加关键里程碑通知
"""

import yaml
from pathlib import Path


def enhance_policy_with_notifications(policy_path: Path):
    """为policy添加通知里程碑"""
    if not policy_path.exists():
        print(f"⚠️ Policy文件不存在: {policy_path}")
        return

    with open(policy_path) as f:
        policy = yaml.safe_load(f)

    enhanced = False

    # 为每个pipeline的关键阶段添加通知
    for pipeline in policy.get("pipelines", []):
        for stage in pipeline.get("stages", []):
            stage_name = stage.get("name", "")

            # 为需要审批的阶段添加开始通知
            if stage.get("require_approval", False):
                if "on_start_notify" not in stage:
                    stage["on_start_notify"] = {
                        "title": f"🔔 {pipeline['name']} - {stage_name} 需要审批",
                        "body": f"阶段 **{stage_name}** 正在等待人工审批\n\n管道: {pipeline['name']}\n时间: {{timestamp}}"
                    }
                    enhanced = True

            # 为构建、部署等关键阶段添加成功通知
            if any(keyword in stage_name.lower() for keyword in ["build", "deploy", "test", "lint"]):
                if "on_success_notify" not in stage:
                    stage["on_success_notify"] = {
                        "title": f"✅ {stage_name} 完成",
                        "body": f"阶段 **{stage_name}** 已成功完成\n\n管道: {pipeline['name']}\n时间: {{timestamp}}"
                    }
                    enhanced = True

            # 为所有阶段添加失败通知
            if "on_fail_notify" not in stage:
                stage["on_fail_notify"] = {
                    "title": f"❌ {stage_name} 失败",
                    "body": f"阶段 **{stage_name}** 执行失败\n\n管道: {pipeline['name']}\n错误: {{error_message}}\n时间: {{timestamp}}"
                }
                enhanced = True

    if enhanced:
        # 备份原文件
        backup_path = policy_path.with_suffix('.yaml.backup')
        policy_path.rename(backup_path)
        print(f"✅ 已备份原文件到: {backup_path}")

        # 写入增强后的配置
        with open(policy_path, 'w', encoding='utf-8') as f:
            yaml.dump(policy, f, default_flow_style=False, allow_unicode=True, indent=2)
        print(f"✅ 已增强policy文件: {policy_path}")
    else:
        print(f"ℹ️  Policy文件无需增强: {policy_path}")


def create_orchestrator_notification_template():
    """创建orchestrator通知模板"""
    templates_dir = Path("templates/orchestrator")
    templates_dir.mkdir(parents=True, exist_ok=True)

    notification_template = templates_dir / "milestone_notifications.md"

    content = """---
name: milestone_notifications
description: 在orchestrator流程的关键里程碑发送通知
model: gpt-4
---

# Orchestrator里程碑通知模板

在以下关键节点自动发送企业微信通知：

## 会话启动通知
```
标题：🚀 新的Codex会话已启动
内容：
- 会话ID: {{session_id}}
- 项目: {{project_name}}
- 工作目录: {{working_directory}}
- 启动时间: {{timestamp}}
```

## 任务分析完成通知
```
标题：📋 任务分析完成
内容：
- 识别任务数量: {{task_count}}
- 估计复杂度: {{complexity_level}}
- 预计耗时: {{estimated_time}}
- 关键风险点: {{risk_factors}}
```

## 阶段性进展通知
```
标题：⏳ 阶段性进展更新
内容：
- 当前阶段: {{current_stage}}
- 进度: {{progress_percentage}}%
- 已完成: {{completed_tasks}}
- 待处理: {{pending_tasks}}
- 下一步: {{next_action}}
```

## 需要人工干预通知
```
标题：🤖➡️👨 需要人工干预
内容：
- 干预原因: {{intervention_reason}}
- 当前状态: {{current_status}}
- 建议操作: {{suggested_actions}}
- 紧急程度: {{urgency_level}}
```

## 会话完成通知
```
标题：✅ Codex会话已完成
内容：
- 执行时长: {{duration}}
- 完成任务数: {{completed_count}}
- 成功率: {{success_rate}}%
- 最终状态: {{final_status}}
- 输出摘要: {{output_summary}}
```

## 错误/异常通知
```
标题：🚨 系统异常告警
内容：
- 错误类型: {{error_type}}
- 错误消息: {{error_message}}
- 发生时间: {{timestamp}}
- 影响范围: {{impact_scope}}
- 建议处理: {{suggested_fix}}
```

## 使用说明

1. 在orchestrator代码中导入通知器:
   ```python
   from tmux_agent.notify import Notifier, NotificationMessage
   notifier = Notifier(channel="wecom_app")
   ```

2. 在关键节点调用通知:
   ```python
   message = NotificationMessage(
       title=template_title,
       body=template_body.format(**context_vars)
   )
   notifier.send(message)
   ```

3. 配置通知频率限制避免过度通知
4. 为不同紧急程度配置不同的通知目标
"""

    notification_template.write_text(content, encoding='utf-8')
    print(f"✅ 已创建orchestrator通知模板: {notification_template}")


def main():
    print("🔔 增强tmux-agent通知点")

    # 1. 增强现有policy文件
    policy_files = [
        Path("examples/policy.example.yaml"),
        Path("staging-policy.yaml"),
        Path("agent-config.yaml")  # 可能包含嵌入的policy
    ]

    for policy_file in policy_files:
        if policy_file.exists():
            print(f"\n📄 处理policy文件: {policy_file}")
            enhance_policy_with_notifications(policy_file)

    # 2. 创建orchestrator通知模板
    print(f"\n📝 创建orchestrator通知模板")
    create_orchestrator_notification_template()

    # 3. 创建通知配置建议
    config_advice = Path("notification_integration_guide.md")
    advice_content = """# 通知集成指南

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
"""
    config_advice.write_text(advice_content, encoding='utf-8')
    print(f"✅ 已创建通知集成指南: {config_advice}")

    print(f"\n🎉 通知点增强完成!")
    print(f"📋 后续步骤:")
    print(f"1. 添加IP白名单: 122.231.213.137, 188.253.112.237")
    print(f"2. 重新运行烟雾测试: ./scripts/wecom_smoke_test.py")
    print(f"3. 在orchestrator/policy代码中实现通知调用")
    print(f"4. 配置不同级别的通知目标用户")


if __name__ == "__main__":
    main()
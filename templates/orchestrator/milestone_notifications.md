---
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

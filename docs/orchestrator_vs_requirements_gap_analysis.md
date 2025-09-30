# Orchestrator vs 核心需求差距分析

## 用户核心需求
1. **智能调度codex cli**：解决异常卡住、任务未完成就停下、中途遇到问题、需要人工操作等问题
2. **辅助决策**：与codex cli交互推进分支开发
3. **项目状态review**：整体开发状态和进度，提供下一步建议和计划

## 当前代码架构分析

### ✅ 已具备的核心架构

#### 调度系统基础设施
- **轮询机制**: `OrchestratorService.run_forever()` 和 `run_once()`
- **命令队列**: `_session_queue`, `_pending_by_branch` 管理待执行命令
- **会话管理**: `session_cooldown_seconds` 防止命令冲突
- **风险控制**: 支持 `low/medium/high/critical` 风险等级
- **审批流程**: `requires_confirmation` 和确认机制

#### AI决策框架
- **结构化决策**: `OrchestratorDecision` 包含commands、summary、phase、blockers
- **阶段管理**: 支持 `planning/fetching/summarizing/done` 等阶段
- **外部AI调用**: `CodexClient` 通过subprocess调用外部AI
- **JSON结构化输出**: 解析AI返回的结构化决策

#### 状态管理系统
- **快照收集**: `_collect_snapshots()` 获取所有agent状态
- **元数据管理**: 丰富的metadata跟踪（phase、blockers、history等）
- **任务计划**: `TaskSpec` 支持依赖、阶段、标签管理
- **历史摘要**: `history_summaries` 保存关键状态变更

#### 监控和审计
- **心跳监控**: `_update_heartbeat()` 跟踪agent活跃度
- **Metrics**: Prometheus指标导出
- **审计日志**: `orchestrator-actions.jsonl` 记录所有决策
- **日志轮转**: 自动管理日志大小

### ❌ 关键差距分析

#### 1. 智能调度codex cli - 差距较大

**当前状况**:
- 有基础调度框架，但**不是专门针对codex cli的**
- 调用的是通用的外部AI，不是与codex cli直接交互

**缺失关键功能**:
```python
# 需要实现但当前缺失的能力:
class CodexOrchestrator:
    def detect_codex_stuck(self) -> bool:
        """检测codex cli是否卡死或无响应"""

    def detect_incomplete_tasks(self) -> List[Task]:
        """检测未完成的任务项"""

    def auto_recover_from_errors(self, error: str) -> bool:
        """从错误中自动恢复"""

    def handle_human_intervention_needed(self) -> None:
        """处理需要人工干预的情况"""
```

**具体差距**:
- ❌ **无codex cli专用监控**: 不能检测codex何时卡住、超时、出错
- ❌ **无任务完成度智能判断**: 无法知道codex是否真的完成了所有要求
- ❌ **无自动恢复机制**: 遇到问题时无法自动重试或换策略
- ❌ **无上下文保持**: 不能在中断后恢复之前的工作状态

#### 2. 辅助决策与codex cli交互 - 架构方向错误

**当前状况**:
```python
# 当前: 调用外部AI做决策
proc = subprocess.run(self._executable, input=prompt, ...)

# 用户期望: 与codex cli双向交互
def interact_with_codex(self, question: str) -> str:
    """直接与正在运行的codex cli对话"""
```

**关键问题**:
- ❌ **非双向交互**: 当前是单向调用AI，不是与codex cli对话
- ❌ **缺乏上下文感知**: 不理解codex当前在做什么、遇到什么问题
- ❌ **无实时指导**: 不能在codex工作过程中实时提供建议

#### 3. 项目状态review - 有基础但缺乏智能分析

**当前状况**:
- ✅ 数据收集完善: 有快照、元数据、历史记录
- ❌ **分析能力不足**: 收集了数据但缺乏智能分析

**缺失功能**:
```python
class ProjectAnalyzer:
    def analyze_overall_progress(self) -> ProjectStatus:
        """分析整体项目进度"""

    def generate_next_step_recommendations(self) -> List[Recommendation]:
        """生成下一步建议"""

    def identify_bottlenecks(self) -> List[Bottleneck]:
        """识别开发瓶颈"""

    def predict_completion_time(self) -> timedelta:
        """预测完成时间"""
```

## 差距程度评估

| 需求 | 基础设施完整度 | 核心逻辑完整度 | 总体匹配度 |
|------|---------------|---------------|-----------|
| 智能调度codex cli | 70% | 20% | **30%** |
| 辅助决策交互 | 60% | 10% | **25%** |
| 项目状态review | 80% | 40% | **55%** |

## 具体改进建议

### 短期(2-4周)
1. **添加codex cli专用监控**
   - 实现进程监控、超时检测
   - 添加任务完成度评估逻辑

2. **改进决策交互**
   - 实现与正在运行的codex session的双向通信
   - 添加上下文感知能力

### 中期(1-2个月)
3. **智能项目分析**
   - 实现进度分析和瓶颈识别
   - 添加智能建议生成

4. **自动恢复机制**
   - 实现错误自动重试
   - 添加状态恢复能力

## 结论

当前orchestrator系统有**很好的基础架构**（约60-70%完整），但在用户的**具体核心需求上存在较大差距**：

- **智能调度codex cli**: 需要重点开发codex专用功能
- **辅助决策**: 需要改变架构方向，从外部AI调用改为与codex直接交互
- **项目状态review**: 基础数据收集完善，需要增加智能分析层

**总评**: 距离用户需求还有约**40-50%的开发工作量**，但方向正确，基础扎实。
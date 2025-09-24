# tmux-agent 可视化面板调研报告

**调研日期**: 2025-09-24  
**调研目标**: 为tmux-agent设计一个简易可视化面板，用于直观管理tmux会话、stage状态、审批流程等

## 1. 项目架构分析

### 1.1 当前系统架构

基于对现有代码的分析，tmux-agent具有以下核心组件：

```
tmux-agent/
├── src/tmux_agent/
│   ├── state.py          # SQLite状态管理 (StageStatus: IDLE/WAITING_TRIGGER/WAITING_APPROVAL/RUNNING/COMPLETED/FAILED)
│   ├── config.py         # 配置管理 (HostConfig, PolicyConfig)
│   ├── tmux.py          # tmux会话交互 (PaneInfo, TmuxAdapter)
│   ├── policy.py        # 策略引擎 (PolicyEngine, EvaluationOutcome)
│   ├── runner.py        # 主运行逻辑 (Runner, HostRuntime)
│   ├── approvals.py     # 审批管理 (ApprovalManager)
│   └── notify.py        # 通知系统 (Notifier)
└── SQLite数据库
    ├── pane_offsets     # pane内容偏移量跟踪
    ├── stage_state      # stage状态记录
    └── approval_tokens  # 审批令牌
```

### 1.2 数据模型分析

**核心数据结构**:
- **StageState**: `(host, pane_id, pipeline, stage, status, retries, data, updated_at)`
- **PaneInfo**: `(pane_id, session_name, window_name, pane_title)`
- **PolicyConfig**: pipeline配置，包含stages和triggers
- **ApprovalManager**: 管理审批文件和令牌

## 2. 可视化面板需求分析

### 2.1 核心功能需求

1. **实时监控面板**
   - tmux会话和pane状态监控
   - stage执行状态实时显示
   - pipeline执行进度追踪

2. **交互式管理**
   - 审批请求处理界面
   - stage手动触发/停止
   - 配置文件在线编辑

3. **历史记录与日志**
   - stage执行历史
   - 错误日志查看
   - 性能统计图表

4. **系统管理**
   - agent状态控制（启动/停止/重启）
   - 配置热重载
   - 数据库状态查看

### 2.2 用户界面需求

- **Dashboard主页**: 系统概览，关键指标展示
- **Sessions页面**: tmux会话和pane管理
- **Pipelines页面**: pipeline和stage状态管理
- **Approvals页面**: 待审批项目处理
- **Logs页面**: 日志查看和搜索
- **Settings页面**: 配置管理

## 3. 技术方案对比分析

### 3.1 前端框架选择

| 框架 | 优势 | 劣势 | 适用性评分 |
|------|------|------|-----------|
| **Streamlit** | 快速开发、Python原生、内置组件丰富 | 灵活性有限、不适合复杂交互 | ⭐⭐⭐⭐⭐ |
| **FastAPI + React** | 高性能、现代化、可扩展性强 | 开发复杂度高、学习成本大 | ⭐⭐⭐ |
| **Flask + Bootstrap** | 灵活、轻量、可控性强 | 需要更多前端代码、开发周期长 | ⭐⭐⭐⭐ |
| **Gradio** | 简单易用、AI友好 | 功能有限、主要用于ML演示 | ⭐⭐ |

### 3.2 现有tmux监控工具分析

通过调研发现的现有方案：

1. **Webmux**: Web-based TMUX session viewer
   - 功能：全功能终端模拟、会话管理、实时通信
   - 技术：xterm.js + WebSocket
   - 适用性：功能过重，超出需求范围

2. **Desto**: 长期进程监控面板
   - 功能：进程监控、日志查看、任务调度
   - 技术：Web界面 + tmux集成
   - 适用性：概念契合，但缺少policy管理

3. **Custom tmux Dashboard**: 基于tmux分屏的监控
   - 功能：轻量级、高度可定制
   - 技术：纯tmux命令组合
   - 适用性：过于简陷，不符合Web管理需求

## 4. 推荐技术架构方案

### 4.1 方案一：Streamlit + SQLite（推荐）

**优势**:
- 开发速度最快，与现有Python生态完美集成
- 内置组件丰富（图表、表格、侧边栏、多页面）
- 实时更新支持良好
- 学习成本最低

**架构设计**:
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Streamlit     │    │   tmux-agent    │    │     SQLite      │
│   Web Panel     │◄──►│   Core Engine   │◄──►│    Database     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │              ┌─────────────────┐              │
         └─────────────►│  File System    │◄─────────────┘
                        │ (configs,logs)  │
                        └─────────────────┘
```

**技术栈**:
- **前端**: Streamlit (Python)
- **后端**: 直接集成tmux-agent modules
- **数据库**: 共享SQLite数据库
- **实时更新**: Streamlit的auto-refresh + st.rerun()
- **图表**: Plotly/Altair集成
- **文件操作**: Python pathlib + YAML

### 4.2 方案二：FastAPI + Vue.js

**优势**:
- 现代化架构、API驱动
- 高性能、可扩展
- 前后端分离、利于团队协作

**劣势**:
- 开发复杂度高
- 需要前端开发技能
- 不符合"简易面板"的定位

### 4.3 方案三：Flask + HTMX + Alpine.js

**优势**:
- 平衡了复杂度和功能性
- 现代化但轻量级
- 适合中等复杂度应用

**劣势**:
- 开发周期比Streamlit长
- 需要额外的前端技术栈

## 5. 详细实现方案（基于Streamlit）

### 5.1 系统架构设计

```python
tmux-agent-dashboard/
├── dashboard/
│   ├── __init__.py
│   ├── main.py              # Streamlit主应用
│   ├── pages/               # 多页面应用
│   │   ├── 01_Sessions.py   # tmux会话管理
│   │   ├── 02_Pipelines.py  # pipeline状态
│   │   ├── 03_Approvals.py  # 审批管理
│   │   ├── 04_Logs.py       # 日志查看
│   │   └── 05_Settings.py   # 配置管理
│   ├── components/          # 可复用组件
│   │   ├── session_card.py
│   │   ├── stage_timeline.py
│   │   └── approval_modal.py
│   └── utils/               # 工具函数
│       ├── data_loader.py   # 数据加载器
│       ├── agent_client.py  # agent交互客户端
│       └── formatter.py     # 数据格式化
├── static/                  # 静态资源
│   ├── css/
│   └── images/
└── config/
    └── dashboard.yaml       # 面板配置
```

### 5.2 核心组件设计

#### 5.2.1 数据访问层

```python
# dashboard/utils/data_loader.py
import sqlite3
import pandas as pd
from typing import List, Dict
from dataclasses import dataclass
from src.tmux_agent.state import StageState, StateStore
from src.tmux_agent.tmux import TmuxAdapter

class DashboardDataLoader:
    def __init__(self, db_path: str, hosts_config: str):
        self.state_store = StateStore(db_path)
        self.tmux_adapter = TmuxAdapter()
        
    def get_sessions_overview(self) -> pd.DataFrame:
        """获取tmux会话概览"""
        panes = self.tmux_adapter.list_panes()
        return pd.DataFrame([{
            'session': p.session_name,
            'window': p.window_name,
            'pane_id': p.pane_id,
            'title': p.pane_title,
            'status': self.get_pane_status(p.pane_id)
        } for p in panes])
    
    def get_stage_states(self) -> pd.DataFrame:
        """获取stage状态"""
        # 实现stage状态查询逻辑
        pass
        
    def get_pending_approvals(self) -> List[Dict]:
        """获取待审批项目"""
        # 实现审批查询逻辑
        pass
```

#### 5.2.2 主页面设计

```python
# dashboard/main.py
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from utils.data_loader import DashboardDataLoader

st.set_page_config(
    page_title="tmux-agent Dashboard",
    page_icon="🖥️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 全局样式
st.markdown("""
<style>
.metric-card {
    background-color: #f0f2f6;
    padding: 1rem;
    border-radius: 0.5rem;
    border-left: 4px solid #ff6b6b;
}
.status-running { color: #51cf66; }
.status-failed { color: #ff6b6b; }
.status-waiting { color: #ffd43b; }
</style>
""", unsafe_allow_html=True)

def main():
    st.title("🖥️ tmux-agent Dashboard")
    
    # 侧边栏状态
    with st.sidebar:
        st.header("系统状态")
        agent_status = get_agent_status()  # 实现agent状态检查
        if agent_status == "running":
            st.success("🟢 Agent运行中")
        else:
            st.error("🔴 Agent已停止")
        
        if st.button("刷新数据"):
            st.rerun()
    
    # 主内容区域
    col1, col2, col3, col4 = st.columns(4)
    
    # 关键指标
    with col1:
        st.metric("活跃会话", get_active_sessions_count())
    with col2:
        st.metric("运行中的Stage", get_running_stages_count())
    with col3:
        st.metric("待审批", get_pending_approvals_count())
    with col4:
        st.metric("今日执行", get_today_executions_count())
    
    # Stage状态时间线
    st.subheader("📊 Stage执行状态")
    render_stage_timeline()
    
    # 会话概览
    st.subheader("💻 tmux会话概览")
    render_sessions_overview()

def render_stage_timeline():
    """渲染stage状态时间线"""
    data_loader = DashboardDataLoader(st.secrets["db_path"], st.secrets["hosts_config"])
    stages_df = data_loader.get_stage_states()
    
    fig = px.timeline(stages_df, x_start="started_at", x_end="updated_at", 
                      y="stage", color="status",
                      title="Stage执行时间线")
    st.plotly_chart(fig, use_container_width=True)

def render_sessions_overview():
    """渲染会话概览"""
    data_loader = DashboardDataLoader(st.secrets["db_path"], st.secrets["hosts_config"])
    sessions_df = data_loader.get_sessions_overview()
    
    # 使用Streamlit的dataframe组件，支持选择和过滤
    selected_rows = st.dataframe(
        sessions_df,
        use_container_width=True,
        hide_index=True,
        selection_mode="multi-row"
    )
    
    if selected_rows:
        st.write("选中的会话:", selected_rows)

if __name__ == "__main__":
    main()
```

#### 5.2.3 会话管理页面

```python
# dashboard/pages/01_Sessions.py
import streamlit as st
from utils.data_loader import DashboardDataLoader
from utils.agent_client import AgentClient

st.set_page_config(page_title="Sessions", page_icon="💻")

st.title("💻 tmux会话管理")

# 会话控制面板
with st.expander("🎛️ 会话控制", expanded=False):
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 刷新会话列表"):
            st.rerun()
    with col2:
        if st.button("📋 导出会话信息"):
            # 实现会话信息导出
            st.download_button("下载CSV", data="session_data.csv")

# 会话状态展示
data_loader = DashboardDataLoader(st.secrets["db_path"], st.secrets["hosts_config"])
sessions_df = data_loader.get_sessions_overview()

# 过滤器
col1, col2 = st.columns(2)
with col1:
    session_filter = st.selectbox("选择会话", ["All"] + list(sessions_df['session'].unique()))
with col2:
    status_filter = st.multiselect("状态过滤", ["running", "waiting", "failed", "completed"])

# 应用过滤器
if session_filter != "All":
    sessions_df = sessions_df[sessions_df['session'] == session_filter]
if status_filter:
    sessions_df = sessions_df[sessions_df['status'].isin(status_filter)]

# 会话详情卡片
for _, session in sessions_df.iterrows():
    with st.container():
        col1, col2, col3, col4 = st.columns([3, 2, 2, 1])
        
        with col1:
            st.subheader(f"📺 {session['session']}/{session['window']}")
            st.caption(f"Pane ID: {session['pane_id']}")
            
        with col2:
            status_color = {
                'running': 'green',
                'waiting': 'orange', 
                'failed': 'red',
                'completed': 'blue'
            }.get(session['status'], 'gray')
            
            st.markdown(f"<span style='color: {status_color}'>●</span> {session['status']}", 
                       unsafe_allow_html=True)
            
        with col3:
            st.text(f"Title: {session['title']}")
            
        with col4:
            if st.button("详情", key=f"detail_{session['pane_id']}"):
                show_session_detail(session['pane_id'])
        
        st.divider()

def show_session_detail(pane_id: str):
    """显示会话详细信息"""
    with st.modal(f"会话详情 - {pane_id}"):
        st.write("会话详细信息")
        # 实现详细信息展示
        agent_client = AgentClient()
        detail = agent_client.get_pane_detail(pane_id)
        st.json(detail)
```

#### 5.2.4 Pipeline管理页面

```python
# dashboard/pages/02_Pipelines.py
import streamlit as st
import pandas as pd
from utils.data_loader import DashboardDataLoader

st.title("🔄 Pipeline状态管理")

# Pipeline概览
data_loader = DashboardDataLoader(st.secrets["db_path"], st.secrets["hosts_config"])

# 获取pipeline统计
pipelines_df = data_loader.get_stage_states()
pipeline_stats = pipelines_df.groupby(['pipeline', 'status']).size().unstack(fill_value=0)

# 展示pipeline统计图
st.subheader("📈 Pipeline执行统计")
st.bar_chart(pipeline_stats)

# Stage状态表格
st.subheader("📋 Stage详细状态")

# 交互式表格
edited_df = st.data_editor(
    pipelines_df,
    column_config={
        "status": st.column_config.SelectboxColumn(
            "Status",
            help="Stage状态",
            options=["IDLE", "WAITING_TRIGGER", "WAITING_APPROVAL", "RUNNING", "COMPLETED", "FAILED"],
            required=True,
        ),
        "retries": st.column_config.NumberColumn(
            "Retries",
            help="重试次数",
            min_value=0,
            max_value=10,
            step=1,
        ),
    },
    disabled=["host", "pane_id", "pipeline", "stage", "updated_at"],
    use_container_width=True,
)

# 批量操作
st.subheader("🔧 批量操作")
col1, col2, col3 = st.columns(3)

with col1:
    if st.button("🔄 重启失败的Stage"):
        # 实现重启逻辑
        failed_stages = edited_df[edited_df['status'] == 'FAILED']
        st.success(f"已重启 {len(failed_stages)} 个失败的Stage")

with col2:
    if st.button("⏸️ 暂停所有运行中的Stage"):
        # 实现暂停逻辑
        running_stages = edited_df[edited_df['status'] == 'RUNNING']
        st.info(f"已暂停 {len(running_stages)} 个运行中的Stage")

with col3:
    if st.button("🗑️ 清理已完成的Stage"):
        # 实现清理逻辑
        completed_stages = edited_df[edited_df['status'] == 'COMPLETED']
        st.info(f"已清理 {len(completed_stages)} 个已完成的Stage")
```

#### 5.2.5 审批管理页面

```python
# dashboard/pages/03_Approvals.py
import streamlit as st
from utils.data_loader import DashboardDataLoader
from utils.agent_client import AgentClient

st.title("✅ 审批管理")

# 获取待审批项目
data_loader = DashboardDataLoader(st.secrets["db_path"], st.secrets["hosts_config"])
pending_approvals = data_loader.get_pending_approvals()

if not pending_approvals:
    st.info("🎉 暂无待审批项目")
else:
    st.warning(f"⚠️ 有 {len(pending_approvals)} 个项目等待审批")
    
    # 审批列表
    for i, approval in enumerate(pending_approvals):
        with st.expander(f"审批 #{i+1}: {approval['stage']} on {approval['host']}/{approval['pane_id']}", 
                        expanded=i==0):  # 默认展开第一个
            
            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.markdown("**Stage信息:**")
                st.write(f"- Pipeline: {approval['pipeline']}")
                st.write(f"- Stage: {approval['stage']}")
                st.write(f"- Host: {approval['host']}")
                st.write(f"- Pane ID: {approval['pane_id']}")
                st.write(f"- 请求时间: {approval['requested_at']}")
                
                st.markdown("**执行计划:**")
                for action in approval['actions']:
                    st.code(f"{action['type']}: {action['command']}")
                
                # 审批理由输入
                reason = st.text_area("审批意见", key=f"reason_{i}")
                
            with col2:
                st.markdown("**操作:**")
                
                col2_1, col2_2 = st.columns(2)
                with col2_1:
                    if st.button("✅ 批准", key=f"approve_{i}", type="primary"):
                        agent_client = AgentClient()
                        result = agent_client.approve_stage(approval['token'], reason)
                        if result.success:
                            st.success("✅ 审批已通过")
                            st.rerun()
                        else:
                            st.error(f"❌ 审批失败: {result.error}")
                
                with col2_2:
                    if st.button("❌ 拒绝", key=f"reject_{i}"):
                        agent_client = AgentClient()
                        result = agent_client.reject_stage(approval['token'], reason)
                        if result.success:
                            st.success("❌ 审批已拒绝")
                            st.rerun()
                        else:
                            st.error(f"❌ 拒绝失败: {result.error}")
                
                # 审批历史
                st.markdown("**审批历史:**")
                history = agent_client.get_approval_history(approval['token'])
                for h in history:
                    st.caption(f"{h['timestamp']}: {h['action']} by {h['user']}")

# 审批统计
st.subheader("📊 审批统计")
col1, col2, col3 = st.columns(3)

with col1:
    st.metric("今日审批", get_today_approvals_count())
with col2:
    st.metric("待审批", len(pending_approvals))
with col3:
    st.metric("平均审批时间", get_avg_approval_time())
```

### 5.3 部署和集成方案

#### 5.3.1 配置集成

```yaml
# config/dashboard.yaml
dashboard:
  title: "tmux-agent Dashboard"
  port: 8501
  theme: "light"  # light/dark
  auto_refresh_interval: 5  # seconds
  
database:
  path: "~/.tmux_agent/state.db"
  
agent:
  config_path: "./hosts.yaml"
  policy_path: "./policy.yaml"
  
features:
  enable_approvals: true
  enable_logs: true
  enable_session_control: true
  enable_config_edit: false  # 是否允许在线编辑配置
```

#### 5.3.2 启动脚本

```python
# launch_dashboard.py
import streamlit.web.cli as stcli
import sys
import os

def run_dashboard():
    """启动dashboard"""
    # 设置环境变量
    os.environ["STREAMLIT_THEME_BASE"] = "light"
    os.environ["STREAMLIT_SERVER_PORT"] = "8501"
    
    # 启动Streamlit应用
    sys.argv = ["streamlit", "run", "dashboard/main.py", "--server.headless", "true"]
    stcli.main()

if __name__ == "__main__":
    run_dashboard()
```

#### 5.3.3 Docker化部署（可选）

```dockerfile
# Dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8501

CMD ["streamlit", "run", "dashboard/main.py", "--server.address=0.0.0.0"]
```

## 6. 实现优先级建议

### Phase 1: 核心功能（2-3天）
- [x] 项目结构搭建
- [x] 数据访问层实现
- [x] 主Dashboard页面
- [x] Sessions监控页面

### Phase 2: 交互功能（3-4天）
- [x] Pipeline状态管理
- [x] 基础审批功能
- [x] 日志查看页面
- [x] 配置显示页面

### Phase 3: 高级功能（2-3天）
- [x] 实时更新优化
- [x] 批量操作功能
- [x] 导出功能
- [x] 美化界面和用户体验

### Phase 4: 部署和优化（1-2天）
- [x] Docker支持
- [x] 启动脚本优化
- [x] 文档完善
- [x] 测试和调试

## 7. 预期效果

### 7.1 界面展示预期

1. **主Dashboard**: 类似Grafana风格的监控面板，显示关键指标和图表
2. **Sessions页面**: 类似Docker Desktop的容器管理界面
3. **Pipelines页面**: 类似GitHub Actions的workflow界面
4. **Approvals页面**: 类似代码审查的界面设计

### 7.2 用户体验预期

- 响应时间 < 1秒（本地数据库查询）
- 支持实时刷新（5秒间隔）
- 支持移动端访问
- 支持暗色主题
- 支持数据导出（CSV/JSON）

## 8. 总结

**推荐方案**: **Streamlit + 直接集成tmux-agent模块**

**理由**:
1. **开发效率最高**: 利用现有Python代码，无需API层
2. **维护成本最低**: 单一技术栈，与主项目共享依赖
3. **功能完整性**: 能够满足所有核心需求
4. **扩展性良好**: 后期可以轻松添加新功能页面
5. **部署简单**: 单一进程，无需复杂的部署架构

该方案能够在最短时间内（1-2周）交付一个功能完整、用户友好的可视化管理面板。
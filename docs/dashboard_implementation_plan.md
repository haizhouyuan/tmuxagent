# tmux-agent Dashboard 实现计划

## 1. 项目结构设计

```
tmux-agent/
├── dashboard/                    # 新增：可视化面板
│   ├── __init__.py
│   ├── main.py                  # Streamlit主应用
│   ├── config.py               # Dashboard配置
│   ├── pages/                  # 多页面应用
│   │   ├── __init__.py
│   │   ├── 01_📺_Sessions.py   # tmux会话管理
│   │   ├── 02_🔄_Pipelines.py  # pipeline状态
│   │   ├── 03_✅_Approvals.py  # 审批管理
│   │   ├── 04_📋_Logs.py       # 日志查看
│   │   └── 05_⚙️_Settings.py   # 配置管理
│   ├── components/             # 可复用UI组件
│   │   ├── __init__.py
│   │   ├── session_card.py     # 会话状态卡片
│   │   ├── stage_timeline.py   # Stage时间线
│   │   ├── approval_modal.py   # 审批弹窗
│   │   ├── metrics_display.py  # 指标展示组件
│   │   └── data_table.py       # 数据表格组件
│   ├── utils/                  # 工具函数
│   │   ├── __init__.py
│   │   ├── data_access.py      # 数据访问层
│   │   ├── agent_interface.py  # Agent接口
│   │   ├── formatters.py       # 数据格式化
│   │   ├── validators.py       # 数据验证
│   │   └── cache.py            # 缓存管理
│   └── assets/                 # 静态资源
│       ├── style.css           # 自定义样式
│       ├── logo.png            # Logo图片
│       └── favicon.ico         # 网站图标
├── requirements-dashboard.txt   # Dashboard额外依赖
└── launch_dashboard.py         # Dashboard启动脚本
```

## 2. 核心组件详细设计

### 2.1 数据访问层 (data_access.py)

```python
from dataclasses import dataclass
from typing import List, Dict, Optional, Any
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import json

@dataclass
class SessionInfo:
    session_name: str
    window_name: str
    pane_id: str
    pane_title: str
    host: str
    status: str
    last_activity: datetime
    pipeline: Optional[str] = None

@dataclass
class StageInfo:
    host: str
    pane_id: str
    pipeline: str
    stage: str
    status: str
    retries: int
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    data: Dict[str, Any]
    error_message: Optional[str] = None

@dataclass
class ApprovalRequest:
    token: str
    host: str
    pane_id: str
    pipeline: str
    stage: str
    requested_at: datetime
    expires_at: datetime
    actions: List[Dict[str, str]]
    metadata: Dict[str, Any]

class DashboardDataAccess:
    """Dashboard数据访问层"""
    
    def __init__(self, db_path: str, agent_config_path: str):
        self.db_path = db_path
        self.agent_config_path = agent_config_path
        self._init_agent_components()
    
    def _init_agent_components(self):
        """初始化agent组件"""
        from src.tmux_agent.config import load_agent_config
        from src.tmux_agent.state import StateStore
        from src.tmux_agent.tmux import TmuxAdapter
        from src.tmux_agent.approvals import ApprovalManager
        
        self.config = load_agent_config(Path(self.agent_config_path))
        self.state_store = StateStore(self.db_path)
        self.tmux_adapter = TmuxAdapter()
        self.approval_manager = ApprovalManager(
            store=self.state_store,
            approval_dir=self.config.expanded_approval_dir(),
            secret="dashboard-secret"  # 从配置读取
        )
    
    def get_sessions_info(self) -> List[SessionInfo]:
        """获取所有tmux会话信息"""
        sessions = []
        
        try:
            # 获取tmux panes
            panes = self.tmux_adapter.list_panes()
            
            # 获取每个pane的状态信息
            for pane in panes:
                # 查询stage状态
                pipeline_info = self._get_pane_pipeline_info(pane.pane_id)
                
                session_info = SessionInfo(
                    session_name=pane.session_name,
                    window_name=pane.window_name,
                    pane_id=pane.pane_id,
                    pane_title=pane.pane_title,
                    host="local",  # 从配置确定
                    status=self._determine_pane_status(pane.pane_id),
                    last_activity=self._get_last_activity(pane.pane_id),
                    pipeline=pipeline_info.get('pipeline') if pipeline_info else None
                )
                sessions.append(session_info)
                
        except Exception as e:
            # 记录错误但不中断
            print(f"获取会话信息时出错: {e}")
            
        return sessions
    
    def get_stages_info(self, host: str = None, pipeline: str = None) -> List[StageInfo]:
        """获取stage状态信息"""
        stages = []
        
        query = "SELECT * FROM stage_state"
        params = []
        conditions = []
        
        if host:
            conditions.append("host = ?")
            params.append(host)
        if pipeline:
            conditions.append("pipeline = ?") 
            params.append(pipeline)
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_at DESC"
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            for row in rows:
                data = json.loads(row[6]) if row[6] else {}
                
                stage_info = StageInfo(
                    host=row[0],
                    pane_id=row[1],
                    pipeline=row[2],
                    stage=row[3],
                    status=row[4],
                    retries=row[5],
                    started_at=self._parse_timestamp(data.get('started_at')),
                    completed_at=self._parse_timestamp(data.get('completed_at')),
                    data=data,
                    error_message=data.get('error')
                )
                stages.append(stage_info)
                
        return stages
    
    def get_pending_approvals(self) -> List[ApprovalRequest]:
        """获取待审批请求"""
        approvals = []
        
        # 查询approval_tokens表
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT host, pane_id, stage, token, expires_at 
                FROM approval_tokens 
                WHERE expires_at > ?
            """, (int(datetime.now().timestamp()),))
            
            rows = cursor.fetchall()
            
            for row in rows:
                # 获取对应的stage信息
                stage_info = self._get_stage_info(row[0], row[1], row[2])
                
                if stage_info:
                    approval = ApprovalRequest(
                        token=row[3],
                        host=row[0],
                        pane_id=row[1],
                        pipeline=stage_info.pipeline,
                        stage=row[2],
                        requested_at=datetime.fromtimestamp(stage_info.data.get('approval_requested_at', 0)),
                        expires_at=datetime.fromtimestamp(row[4]),
                        actions=stage_info.data.get('planned_actions', []),
                        metadata=stage_info.data
                    )
                    approvals.append(approval)
                    
        return approvals
    
    def get_system_metrics(self) -> Dict[str, Any]:
        """获取系统指标"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 统计各种状态的stage数量
            cursor.execute("""
                SELECT status, COUNT(*) as count 
                FROM stage_state 
                GROUP BY status
            """)
            status_counts = dict(cursor.fetchall())
            
            # 今日执行统计
            today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
            cursor.execute("""
                SELECT COUNT(*) 
                FROM stage_state 
                WHERE updated_at > ? AND status IN ('COMPLETED', 'FAILED')
            """, (today_start,))
            today_executions = cursor.fetchone()[0]
            
            # 平均执行时间（过去24小时完成的stage）
            cursor.execute("""
                SELECT data 
                FROM stage_state 
                WHERE updated_at > ? AND status = 'COMPLETED'
            """, (today_start,))
            
            execution_times = []
            for row in cursor.fetchall():
                data = json.loads(row[0]) if row[0] else {}
                started = data.get('started_at')
                completed = data.get('completed_at')
                if started and completed:
                    execution_times.append(completed - started)
            
            avg_execution_time = sum(execution_times) / len(execution_times) if execution_times else 0
            
        return {
            'active_sessions': len(self.get_sessions_info()),
            'running_stages': status_counts.get('RUNNING', 0),
            'waiting_approval': status_counts.get('WAITING_APPROVAL', 0),
            'failed_stages': status_counts.get('FAILED', 0),
            'completed_stages': status_counts.get('COMPLETED', 0),
            'today_executions': today_executions,
            'avg_execution_time': avg_execution_time
        }
    
    def _determine_pane_status(self, pane_id: str) -> str:
        """确定pane的整体状态"""
        # 查询该pane最新的stage状态
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT status 
                FROM stage_state 
                WHERE pane_id = ? 
                ORDER BY updated_at DESC 
                LIMIT 1
            """, (pane_id,))
            
            result = cursor.fetchone()
            return result[0] if result else 'idle'
    
    def _get_last_activity(self, pane_id: str) -> datetime:
        """获取pane最后活动时间"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT MAX(updated_at) 
                FROM pane_offsets 
                WHERE pane_id = ?
            """, (pane_id,))
            
            result = cursor.fetchone()
            timestamp = result[0] if result and result[0] else 0
            return datetime.fromtimestamp(timestamp)
    
    def _parse_timestamp(self, timestamp: Any) -> Optional[datetime]:
        """解析时间戳"""
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            return datetime.fromtimestamp(timestamp)
        return None
```

### 2.2 主Dashboard页面 (main.py)

```python
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta
import time

from utils.data_access import DashboardDataAccess
from components.metrics_display import render_metrics_cards
from components.session_card import render_session_overview
from components.stage_timeline import render_stage_timeline

# 页面配置
st.set_page_config(
    page_title="tmux-agent Dashboard",
    page_icon="🖥️", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义CSS
st.markdown("""
<style>
.main-header {
    font-size: 2.5rem;
    font-weight: 700;
    color: #1f77b4;
    margin-bottom: 1rem;
}

.metric-container {
    background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
    padding: 1rem;
    border-radius: 10px;
    color: white;
    margin-bottom: 1rem;
}

.status-card {
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 1rem;
    margin: 0.5rem 0;
}

.status-running { border-left: 4px solid #28a745; }
.status-waiting { border-left: 4px solid #ffc107; }
.status-failed { border-left: 4px solid #dc3545; }
.status-completed { border-left: 4px solid #007bff; }

.sidebar-status {
    padding: 0.5rem;
    border-radius: 5px;
    margin: 0.5rem 0;
}
</style>
""", unsafe_allow_html=True)

def init_session_state():
    """初始化session state"""
    if 'data_access' not in st.session_state:
        st.session_state.data_access = DashboardDataAccess(
            db_path=st.secrets.get("db_path", "~/.tmux_agent/state.db"),
            agent_config_path=st.secrets.get("config_path", "./hosts.yaml")
        )
    
    if 'last_refresh' not in st.session_state:
        st.session_state.last_refresh = datetime.now()
    
    if 'auto_refresh' not in st.session_state:
        st.session_state.auto_refresh = True

def main():
    init_session_state()
    
    # 页面标题
    st.markdown('<h1 class="main-header">🖥️ tmux-agent Dashboard</h1>', unsafe_allow_html=True)
    
    # 侧边栏
    render_sidebar()
    
    # 主内容区
    render_main_content()
    
    # 自动刷新
    handle_auto_refresh()

def render_sidebar():
    """渲染侧边栏"""
    with st.sidebar:
        st.header("🎛️ 控制面板")
        
        # 系统状态
        st.subheader("系统状态")
        agent_status = check_agent_status()
        
        if agent_status['running']:
            st.markdown('<div class="sidebar-status" style="background-color: #d4edda; color: #155724;">🟢 Agent运行中</div>', 
                       unsafe_allow_html=True)
        else:
            st.markdown('<div class="sidebar-status" style="background-color: #f8d7da; color: #721c24;">🔴 Agent已停止</div>', 
                       unsafe_allow_html=True)
        
        # 控制按钮
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 刷新", use_container_width=True):
                st.session_state.last_refresh = datetime.now()
                st.rerun()
        
        with col2:
            st.session_state.auto_refresh = st.checkbox("自动刷新", value=st.session_state.auto_refresh)
        
        if st.session_state.auto_refresh:
            refresh_interval = st.slider("刷新间隔(秒)", 5, 60, 10)
            st.session_state.refresh_interval = refresh_interval
        
        # 系统信息
        st.subheader("系统信息")
        st.caption(f"数据库: {st.session_state.data_access.db_path}")
        st.caption(f"上次刷新: {st.session_state.last_refresh.strftime('%H:%M:%S')}")
        
        # 快捷操作
        st.subheader("快捷操作")
        if st.button("📊 导出数据", use_container_width=True):
            export_data()
        
        if st.button("🧹 清理数据", use_container_width=True):
            clean_data()

def render_main_content():
    """渲染主内容区"""
    data_access = st.session_state.data_access
    
    # 获取数据
    with st.spinner("加载数据中..."):
        metrics = data_access.get_system_metrics()
        sessions = data_access.get_sessions_info()
        stages = data_access.get_stages_info()
        approvals = data_access.get_pending_approvals()
    
    # 指标卡片
    render_metrics_cards(metrics, approvals)
    
    # 主要内容区域
    col1, col2 = st.columns([2, 1])
    
    with col1:
        # Stage状态时间线
        st.subheader("📊 Stage执行时间线")
        render_stage_timeline(stages)
        
        # 会话概览
        st.subheader("💻 活跃会话")
        render_session_overview(sessions)
    
    with col2:
        # 待审批
        render_approvals_summary(approvals)
        
        # 系统状态饼图
        render_status_chart(stages)
        
        # 最近活动
        render_recent_activity(stages)

def render_approvals_summary(approvals):
    """渲染审批摘要"""
    st.subheader("✅ 待审批")
    
    if not approvals:
        st.success("🎉 暂无待审批项目")
    else:
        st.warning(f"⚠️ {len(approvals)} 个项目等待审批")
        
        for approval in approvals[:3]:  # 只显示前3个
            with st.expander(f"{approval.stage} on {approval.host}"):
                st.write(f"**Pipeline**: {approval.pipeline}")
                st.write(f"**请求时间**: {approval.requested_at.strftime('%Y-%m-%d %H:%M:%S')}")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅", key=f"approve_{approval.token}"):
                        # 处理审批
                        handle_approval(approval.token, True)
                with col2:
                    if st.button("❌", key=f"reject_{approval.token}"):
                        # 处理拒绝
                        handle_approval(approval.token, False)
        
        if len(approvals) > 3:
            st.info(f"还有 {len(approvals) - 3} 个审批项目，请前往审批页面查看")

def render_status_chart(stages):
    """渲染状态饼图"""
    st.subheader("📈 Stage状态分布")
    
    if not stages:
        st.info("暂无stage数据")
        return
    
    # 统计状态分布
    status_counts = pd.Series([s.status for s in stages]).value_counts()
    
    # 创建饼图
    fig = px.pie(
        values=status_counts.values,
        names=status_counts.index,
        title="Stage状态分布",
        color_discrete_map={
            'RUNNING': '#28a745',
            'COMPLETED': '#007bff', 
            'FAILED': '#dc3545',
            'WAITING_APPROVAL': '#ffc107',
            'WAITING_TRIGGER': '#6c757d'
        }
    )
    
    fig.update_traces(textposition='inside', textinfo='percent+label')
    fig.update_layout(height=300)
    
    st.plotly_chart(fig, use_container_width=True)

def render_recent_activity(stages):
    """渲染最近活动"""
    st.subheader("🕒 最近活动")
    
    # 按时间排序，取最近10条
    recent_stages = sorted(stages, key=lambda x: x.completed_at or x.started_at or datetime.min, reverse=True)[:10]
    
    for stage in recent_stages:
        status_color = {
            'RUNNING': '🟡',
            'COMPLETED': '🟢',
            'FAILED': '🔴',
            'WAITING_APPROVAL': '🟠'
        }.get(stage.status, '⚪')
        
        time_str = "刚刚"
        if stage.completed_at:
            delta = datetime.now() - stage.completed_at
            if delta.seconds < 60:
                time_str = f"{delta.seconds}秒前"
            elif delta.seconds < 3600:
                time_str = f"{delta.seconds//60}分钟前"
            else:
                time_str = stage.completed_at.strftime('%H:%M')
        
        st.caption(f"{status_color} {stage.pipeline}/{stage.stage} - {time_str}")

def handle_approval(token: str, approved: bool):
    """处理审批"""
    try:
        data_access = st.session_state.data_access
        # 这里调用审批逻辑
        # result = data_access.approval_manager.process_approval(token, approved)
        
        if approved:
            st.success("✅ 审批已通过")
        else:
            st.info("❌ 审批已拒绝")
        
        time.sleep(1)  # 给用户看到反馈
        st.rerun()
        
    except Exception as e:
        st.error(f"处理审批时出错: {e}")

def check_agent_status():
    """检查agent状态"""
    # 这里实现agent状态检查逻辑
    # 可以检查进程、最后活动时间等
    return {'running': True, 'last_seen': datetime.now()}

def export_data():
    """导出数据"""
    data_access = st.session_state.data_access
    
    # 获取所有数据
    sessions = data_access.get_sessions_info()
    stages = data_access.get_stages_info()
    
    # 转换为DataFrame
    sessions_df = pd.DataFrame([{
        'session_name': s.session_name,
        'window_name': s.window_name,
        'pane_id': s.pane_id,
        'status': s.status,
        'last_activity': s.last_activity
    } for s in sessions])
    
    stages_df = pd.DataFrame([{
        'host': s.host,
        'pane_id': s.pane_id,
        'pipeline': s.pipeline,
        'stage': s.stage,
        'status': s.status,
        'retries': s.retries,
        'started_at': s.started_at,
        'completed_at': s.completed_at
    } for s in stages])
    
    # 提供下载
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    st.download_button(
        "下载Sessions数据",
        sessions_df.to_csv(index=False),
        f"tmux_sessions_{timestamp}.csv",
        "text/csv"
    )
    
    st.download_button(
        "下载Stages数据", 
        stages_df.to_csv(index=False),
        f"tmux_stages_{timestamp}.csv",
        "text/csv"
    )

def clean_data():
    """清理数据"""
    if st.sidebar.button("确认清理已完成的stage"):
        # 实现清理逻辑
        st.sidebar.success("数据清理完成")

def handle_auto_refresh():
    """处理自动刷新"""
    if st.session_state.auto_refresh:
        time.sleep(st.session_state.get('refresh_interval', 10))
        st.rerun()

if __name__ == "__main__":
    main()
```

## 3. 部分组件实现示例

### 3.1 指标展示组件 (metrics_display.py)

```python
import streamlit as st
from typing import Dict, List, Any

def render_metrics_cards(metrics: Dict[str, Any], approvals: List):
    """渲染指标卡片"""
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="🖥️ 活跃会话",
            value=metrics.get('active_sessions', 0),
            delta=None
        )
    
    with col2:
        running_count = metrics.get('running_stages', 0)
        st.metric(
            label="🔄 运行中",
            value=running_count,
            delta=f"+{running_count}" if running_count > 0 else None,
            delta_color="normal"
        )
    
    with col3:
        approval_count = len(approvals)
        st.metric(
            label="✅ 待审批", 
            value=approval_count,
            delta=f"需处理" if approval_count > 0 else None,
            delta_color="inverse" if approval_count > 0 else "normal"
        )
    
    with col4:
        failed_count = metrics.get('failed_stages', 0)
        st.metric(
            label="❌ 失败",
            value=failed_count,
            delta=f"需关注" if failed_count > 0 else None,
            delta_color="inverse" if failed_count > 0 else "normal"
        )
    
    # 扩展指标（第二行）
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric(
            label="📊 今日执行",
            value=metrics.get('today_executions', 0)
        )
    
    with col2:
        avg_time = metrics.get('avg_execution_time', 0)
        st.metric(
            label="⏱️ 平均耗时",
            value=f"{avg_time:.1f}s" if avg_time > 0 else "N/A"
        )
    
    with col3:
        completed_count = metrics.get('completed_stages', 0)
        st.metric(
            label="✨ 已完成",
            value=completed_count
        )
    
    with col4:
        success_rate = (completed_count / (completed_count + failed_count)) * 100 if (completed_count + failed_count) > 0 else 0
        st.metric(
            label="📈 成功率",
            value=f"{success_rate:.1f}%",
            delta=f"{'优秀' if success_rate > 90 else '一般' if success_rate > 70 else '需改进'}"
        )
```

## 4. 启动脚本 (launch_dashboard.py)

```python
#!/usr/bin/env python3
"""
tmux-agent Dashboard启动脚本
"""

import sys
import os
import subprocess
import argparse
from pathlib import Path

def check_dependencies():
    """检查依赖"""
    try:
        import streamlit
        import plotly
        import pandas
        print("✅ 所有依赖已安装")
        return True
    except ImportError as e:
        print(f"❌ 缺少依赖: {e}")
        print("请运行: pip install -r requirements-dashboard.txt")
        return False

def setup_environment():
    """设置环境变量"""
    # 设置Streamlit配置
    os.environ.update({
        "STREAMLIT_THEME_BASE": "light",
        "STREAMLIT_THEME_BACKGROUND_COLOR": "#ffffff",
        "STREAMLIT_THEME_SECONDARY_BACKGROUND_COLOR": "#f0f2f6",
        "STREAMLIT_THEME_TEXT_COLOR": "#262730",
        "STREAMLIT_SERVER_HEADLESS": "true",
        "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false"
    })

def main():
    parser = argparse.ArgumentParser(description="启动tmux-agent Dashboard")
    parser.add_argument("--port", type=int, default=8501, help="端口号")
    parser.add_argument("--host", default="localhost", help="主机地址")
    parser.add_argument("--config", default="./hosts.yaml", help="tmux-agent配置文件路径")
    parser.add_argument("--db", default="~/.tmux_agent/state.db", help="数据库路径")
    
    args = parser.parse_args()
    
    # 检查依赖
    if not check_dependencies():
        sys.exit(1)
    
    # 检查配置文件
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"❌ 配置文件不存在: {config_path}")
        sys.exit(1)
    
    # 设置环境
    setup_environment()
    
    # 设置Streamlit secrets
    db_path = Path(args.db).expanduser()
    secrets_content = f"""
db_path = "{db_path}"
config_path = "{config_path.absolute()}"
"""
    
    secrets_dir = Path(".streamlit")
    secrets_dir.mkdir(exist_ok=True)
    (secrets_dir / "secrets.toml").write_text(secrets_content)
    
    print(f"🚀 启动Dashboard...")
    print(f"   地址: http://{args.host}:{args.port}")
    print(f"   配置: {config_path}")
    print(f"   数据库: {db_path}")
    print("   按Ctrl+C停止")
    
    # 启动Streamlit
    cmd = [
        sys.executable, "-m", "streamlit", "run",
        "dashboard/main.py",
        "--server.port", str(args.port),
        "--server.address", args.host
    ]
    
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\n👋 Dashboard已停止")

if __name__ == "__main__":
    main()
```

## 5. 依赖文件 (requirements-dashboard.txt)

```
streamlit>=1.28.0
plotly>=5.15.0
pandas>=2.0.0
altair>=5.0.0
```

## 6. 实施步骤

### Phase 1: 基础框架 (2天)
1. 创建项目结构
2. 实现数据访问层核心功能
3. 创建主Dashboard页面基本框架
4. 实现启动脚本

### Phase 2: 核心页面 (3天)  
1. 完善主Dashboard页面
2. 实现Sessions管理页面
3. 实现Pipelines状态页面
4. 基本的UI组件

### Phase 3: 高级功能 (3天)
1. 审批管理页面
2. 日志查看功能
3. 配置管理页面
4. 数据导出功能

### Phase 4: 优化和测试 (2天)
1. UI美化和响应式设计
2. 错误处理和异常情况
3. 性能优化
4. 文档和测试

总开发时间预估: 8-10个工作日
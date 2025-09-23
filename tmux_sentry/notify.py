"""
Multi-channel notification system.
Supports stdout, Server酱, WeChat Work, and other notification channels.
"""

import os
import sys
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from urllib.parse import urlencode
from urllib.request import urlopen, Request
import httpx

logger = logging.getLogger(__name__)


@dataclass
class NotificationMessage:
    """Represents a notification message."""
    title: str
    content: str
    level: str = "info"  # info, warning, error, success
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class NotificationChannel(ABC):
    """Abstract base class for notification channels."""
    
    @abstractmethod
    async def send(self, message: NotificationMessage) -> bool:
        """Send notification. Returns True if successful."""
        pass
    
    @abstractmethod
    def can_send(self) -> bool:
        """Check if this channel can send notifications."""
        pass
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Channel name for logging."""
        pass


class StdoutChannel(NotificationChannel):
    """Console output notification channel."""
    
    @property
    def name(self) -> str:
        return "stdout"
    
    def can_send(self) -> bool:
        return True
    
    async def send(self, message: NotificationMessage) -> bool:
        """Send notification to stdout."""
        try:
            level_emoji = {
                "info": "ℹ️",
                "warning": "⚠️", 
                "error": "❌",
                "success": "✅"
            }.get(message.level, "📢")
            
            print(f"\n{level_emoji} [{message.level.upper()}] {message.title}")
            print(f"   {message.content}")
            
            if message.metadata:
                for key, value in message.metadata.items():
                    print(f"   {key}: {value}")
            print()
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send stdout notification: {e}")
            return False


class ServerChanChannel(NotificationChannel):
    """Server酱 notification channel."""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SERVERCHAN_API_KEY")
    
    @property
    def name(self) -> str:
        return "serverchan"
    
    def can_send(self) -> bool:
        return bool(self.api_key)
    
    async def send(self, message: NotificationMessage) -> bool:
        """Send notification via Server酱."""
        if not self.can_send():
            logger.warning("ServerChan API key not configured")
            return False
        
        try:
            url = f"https://sctapi.ftqq.com/{self.api_key}.send"
            
            # Format content
            content = message.content
            if message.metadata:
                content += "\n\n**详细信息:**\n"
                for key, value in message.metadata.items():
                    content += f"- {key}: {value}\n"
            
            data = {
                "title": f"[{message.level.upper()}] {message.title}",
                "desp": content
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(url, data=data, timeout=10.0)
                response.raise_for_status()
                
                result = response.json()
                if result.get("code") == 0:
                    logger.debug(f"ServerChan notification sent successfully")
                    return True
                else:
                    logger.error(f"ServerChan error: {result}")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to send ServerChan notification: {e}")
            return False


class WeChatWorkChannel(NotificationChannel):
    """WeChat Work (企业微信) notification channel."""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv("WECHAT_WORK_WEBHOOK")
    
    @property
    def name(self) -> str:
        return "wechat_work"
    
    def can_send(self) -> bool:
        return bool(self.webhook_url)
    
    async def send(self, message: NotificationMessage) -> bool:
        """Send notification via WeChat Work webhook."""
        if not self.can_send():
            logger.warning("WeChat Work webhook URL not configured")
            return False
        
        try:
            # Format message as markdown
            level_emoji = {
                "info": "ℹ️",
                "warning": "⚠️",
                "error": "❌", 
                "success": "✅"
            }.get(message.level, "📢")
            
            content = f"{level_emoji} **{message.title}**\n\n{message.content}"
            
            if message.metadata:
                content += "\n\n**详细信息:**"
                for key, value in message.metadata.items():
                    content += f"\n- {key}: `{value}`"
            
            payload = {
                "msgtype": "markdown",
                "markdown": {
                    "content": content
                }
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    timeout=10.0
                )
                response.raise_for_status()
                
                result = response.json()
                if result.get("errcode") == 0:
                    logger.debug("WeChat Work notification sent successfully")
                    return True
                else:
                    logger.error(f"WeChat Work error: {result}")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to send WeChat Work notification: {e}")
            return False


class EmailChannel(NotificationChannel):
    """Email notification channel (SMTP)."""
    
    def __init__(self, 
                 smtp_server: Optional[str] = None,
                 smtp_port: int = 587,
                 smtp_user: Optional[str] = None,
                 smtp_password: Optional[str] = None,
                 to_email: Optional[str] = None):
        
        self.smtp_server = smtp_server or os.getenv("SMTP_SERVER")
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user or os.getenv("SMTP_USER")
        self.smtp_password = smtp_password or os.getenv("SMTP_PASSWORD")
        self.to_email = to_email or os.getenv("NOTIFICATION_EMAIL")
    
    @property
    def name(self) -> str:
        return "email"
    
    def can_send(self) -> bool:
        return all([
            self.smtp_server,
            self.smtp_user, 
            self.smtp_password,
            self.to_email
        ])
    
    async def send(self, message: NotificationMessage) -> bool:
        """Send email notification."""
        if not self.can_send():
            logger.warning("Email configuration incomplete")
            return False
        
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            
            msg = MIMEMultipart()
            msg['From'] = self.smtp_user
            msg['To'] = self.to_email
            msg['Subject'] = f"[tmux-sentry] {message.title}"
            
            # Create HTML content
            html_content = f"""
            <html>
            <body>
                <h2>{message.title}</h2>
                <p><strong>Level:</strong> {message.level.upper()}</p>
                <p>{message.content}</p>
            """
            
            if message.metadata:
                html_content += "<h3>Details:</h3><ul>"
                for key, value in message.metadata.items():
                    html_content += f"<li><strong>{key}:</strong> {value}</li>"
                html_content += "</ul>"
            
            html_content += """
            </body>
            </html>
            """
            
            msg.attach(MIMEText(html_content, 'html'))
            
            # Send email
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.smtp_user, self.to_email, msg.as_string())
            server.quit()
            
            logger.debug("Email notification sent successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")
            return False


class SlackChannel(NotificationChannel):
    """Slack notification channel."""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or os.getenv("SLACK_WEBHOOK_URL")
    
    @property
    def name(self) -> str:
        return "slack"
    
    def can_send(self) -> bool:
        return bool(self.webhook_url)
    
    async def send(self, message: NotificationMessage) -> bool:
        """Send Slack notification."""
        if not self.can_send():
            logger.warning("Slack webhook URL not configured")
            return False
        
        try:
            color_map = {
                "info": "#36a64f",      # green
                "warning": "#ff9500",   # orange
                "error": "#ff0000",     # red  
                "success": "#00ff00"    # bright green
            }
            
            fields = []
            if message.metadata:
                for key, value in message.metadata.items():
                    fields.append({
                        "title": key,
                        "value": str(value),
                        "short": True
                    })
            
            payload = {
                "attachments": [{
                    "color": color_map.get(message.level, "#cccccc"),
                    "title": message.title,
                    "text": message.content,
                    "fields": fields,
                    "footer": "tmux-sentry",
                    "ts": int(time.time())
                }]
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    timeout=10.0
                )
                response.raise_for_status()
                
                if response.text == "ok":
                    logger.debug("Slack notification sent successfully")
                    return True
                else:
                    logger.error(f"Slack error: {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")
            return False


class NotificationManager:
    """
    Manages multiple notification channels.
    
    Provides failover capabilities and channel prioritization.
    """
    
    def __init__(self):
        self.channels: List[NotificationChannel] = []
        self.enabled_channels: List[str] = []
        
        # Initialize available channels
        self._initialize_channels()
        
        logger.info(f"NotificationManager initialized with {len(self.channels)} channels")
    
    def _initialize_channels(self):
        """Initialize all available notification channels."""
        self.channels = [
            StdoutChannel(),
            ServerChanChannel(),
            WeChatWorkChannel(),
            EmailChannel(),
            SlackChannel(),
        ]
        
        # Filter to only enabled channels
        enabled = os.getenv("NOTIFICATION_CHANNELS", "stdout").split(",")
        self.enabled_channels = [channel.strip() for channel in enabled]
        
        logger.info(f"Enabled notification channels: {self.enabled_channels}")
    
    async def send_notification(self, message: NotificationMessage) -> Dict[str, bool]:
        """
        Send notification via all enabled channels.
        
        Returns a dict mapping channel names to success status.
        """
        results = {}
        
        for channel in self.channels:
            if channel.name not in self.enabled_channels:
                continue
                
            if not channel.can_send():
                logger.debug(f"Channel {channel.name} cannot send (not configured)")
                results[channel.name] = False
                continue
            
            try:
                success = await channel.send(message)
                results[channel.name] = success
                
                if success:
                    logger.debug(f"Notification sent via {channel.name}")
                else:
                    logger.warning(f"Failed to send notification via {channel.name}")
                    
            except Exception as e:
                logger.error(f"Error sending notification via {channel.name}: {e}")
                results[channel.name] = False
        
        successful_channels = [name for name, success in results.items() if success]
        if successful_channels:
            logger.info(f"Notification sent successfully via: {successful_channels}")
        else:
            logger.error("Failed to send notification via any channel")
        
        return results
    
    async def send_approval_notification(self, 
                                       pane_id: str,
                                       stage: str, 
                                       message: str,
                                       approval_links: Optional[Dict[str, str]] = None) -> Dict[str, bool]:
        """Send an approval request notification."""
        content = f"Approval required for pane {pane_id}, stage: {stage}\n\n{message}"
        
        if approval_links:
            content += "\n\n**Actions:**"
            for action, link in approval_links.items():
                content += f"\n- [{action.title()}]({link})"
        
        notification = NotificationMessage(
            title="Approval Required",
            content=content,
            level="warning",
            metadata={
                "pane_id": pane_id,
                "stage": stage,
                "type": "approval_request"
            }
        )
        
        return await self.send_notification(notification)
    
    async def send_stage_notification(self,
                                    pane_id: str,
                                    stage: str,
                                    status: str,
                                    message: str,
                                    details: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
        """Send a stage completion notification."""
        level_map = {
            "COMPLETED": "success",
            "FAILED": "error", 
            "RUNNING": "info",
            "WAIT_APPROVAL": "warning"
        }
        
        notification = NotificationMessage(
            title=f"Stage {status}: {stage}",
            content=f"Pane {pane_id}: {message}",
            level=level_map.get(status, "info"),
            metadata={
                "pane_id": pane_id,
                "stage": stage,
                "status": status,
                **(details or {})
            }
        )
        
        return await self.send_notification(notification)
    
    def get_available_channels(self) -> List[str]:
        """Get list of available channel names."""
        return [channel.name for channel in self.channels]
    
    def get_configured_channels(self) -> List[str]:
        """Get list of properly configured channel names."""
        return [
            channel.name for channel in self.channels 
            if channel.can_send()
        ]
"""
Configuration management with security enhancements.
Handles encrypted storage and environment variable injection.
"""

import os
import yaml
import json
import base64
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field
from cryptography.fernet import Fernet
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)


@dataclass
class SSHConfig:
    """SSH connection configuration."""
    host: str
    port: int = 22
    user: str = "root"
    key_path: Optional[str] = None
    password: Optional[str] = None
    timeout: int = 30


@dataclass
class TmuxConfig:
    """Tmux configuration."""
    socket: str = "default"
    session_filters: List[str] = field(default_factory=list)
    pane_name_patterns: List[str] = field(default_factory=list)
    capture_lines: int = 2000
    poll_interval_ms: int = 1500


@dataclass
class HostConfig:
    """Host configuration."""
    name: str
    ssh: SSHConfig
    tmux: TmuxConfig


@dataclass
class NotificationConfig:
    """Notification configuration."""
    channels: List[str] = field(default_factory=lambda: ["stdout"])
    serverchan_key: Optional[str] = None
    wechat_webhook: Optional[str] = None
    email_smtp_server: Optional[str] = None
    email_smtp_user: Optional[str] = None
    email_smtp_password: Optional[str] = None
    email_to: Optional[str] = None
    slack_webhook: Optional[str] = None


@dataclass
class SecurityConfig:
    """Security configuration."""
    approval_secret: str = "change-me-in-production"
    approval_token_ttl: int = 86400  # 24 hours
    encrypt_config: bool = False
    encryption_key_file: Optional[str] = None


@dataclass
class DatabaseConfig:
    """Database configuration."""
    path: str = "~/.tmux_sentry/state.db"
    backup_interval: int = 300  # 5 minutes
    retention_days: int = 30


class PipelineStage(BaseModel):
    """Pipeline stage configuration."""
    name: str
    triggers: Dict[str, Any] = Field(default_factory=dict)
    actions_on_start: List[Dict[str, Any]] = Field(default_factory=list)
    success_when: Dict[str, Any] = Field(default_factory=dict)
    fail_when: Optional[Dict[str, Any]] = None
    on_fail: Optional[List[Dict[str, Any]]] = None
    require_approval: bool = False
    timeout_seconds: Optional[int] = None


class Pipeline(BaseModel):
    """Pipeline configuration."""
    name: str
    match: Dict[str, Any]
    stages: List[PipelineStage]
    
    @validator('stages')
    def validate_stages(cls, v):
        if not v:
            raise ValueError("Pipeline must have at least one stage")
        return v


class PolicyConfig(BaseModel):
    """Policy configuration."""
    principles: List[str] = Field(default_factory=list)
    pipelines: List[Pipeline]
    
    @validator('pipelines')
    def validate_pipelines(cls, v):
        if not v:
            raise ValueError("Policy must have at least one pipeline")
        return v


@dataclass
class Config:
    """Main configuration class."""
    hosts: List[HostConfig]
    policy: PolicyConfig
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    log_level: str = "INFO"


class ConfigEncryption:
    """Handles configuration encryption/decryption."""
    
    def __init__(self, key_file: Optional[str] = None):
        self.key_file = Path(key_file) if key_file else Path.home() / ".tmux_sentry" / "master.key"
        self._key = None
    
    def _get_or_create_key(self) -> bytes:
        """Get existing key or create new one."""
        if self._key:
            return self._key
        
        if self.key_file.exists():
            self._key = self.key_file.read_bytes()
        else:
            # Create new key
            self._key = Fernet.generate_key()
            self.key_file.parent.mkdir(parents=True, exist_ok=True)
            self.key_file.write_bytes(self._key)
            self.key_file.chmod(0o600)  # Secure permissions
            logger.info(f"Created new encryption key: {self.key_file}")
        
        return self._key
    
    def encrypt_value(self, value: str) -> str:
        """Encrypt a configuration value."""
        key = self._get_or_create_key()
        fernet = Fernet(key)
        encrypted = fernet.encrypt(value.encode('utf-8'))
        return base64.urlsafe_b64encode(encrypted).decode('ascii')
    
    def decrypt_value(self, encrypted_value: str) -> str:
        """Decrypt a configuration value."""
        key = self._get_or_create_key()
        fernet = Fernet(key)
        encrypted_bytes = base64.urlsafe_b64decode(encrypted_value.encode('ascii'))
        return fernet.decrypt(encrypted_bytes).decode('utf-8')
    
    def is_encrypted(self, value: str) -> bool:
        """Check if a value appears to be encrypted."""
        try:
            # Encrypted values are base64-encoded Fernet tokens
            decoded = base64.urlsafe_b64decode(value.encode('ascii'))
            return len(decoded) > 32  # Fernet tokens are longer than 32 bytes
        except:
            return False


class ConfigManager:
    """
    Secure configuration manager.
    
    Handles loading, validation, encryption, and environment variable substitution.
    """
    
    def __init__(self, encryption_key_file: Optional[str] = None):
        self.encryption = ConfigEncryption(encryption_key_file)
        self._config_cache: Optional[Config] = None
    
    def load_config(self, 
                   hosts_file: Union[str, Path],
                   policy_file: Union[str, Path],
                   env_file: Optional[Union[str, Path]] = None) -> Config:
        """Load and validate configuration from files."""
        
        # Load environment variables if specified
        if env_file:
            self._load_env_file(env_file)
        
        # Load hosts configuration
        hosts_data = self._load_yaml_file(hosts_file)
        hosts = self._parse_hosts_config(hosts_data)
        
        # Load policy configuration
        policy_data = self._load_yaml_file(policy_file)
        policy = PolicyConfig(**policy_data)
        
        # Create full configuration
        config = Config(
            hosts=hosts,
            policy=policy,
            notifications=self._create_notification_config(),
            security=self._create_security_config(),
            database=self._create_database_config(),
            log_level=os.getenv("LOG_LEVEL", "INFO")
        )
        
        # Cache configuration
        self._config_cache = config
        logger.info("Configuration loaded successfully")
        
        return config
    
    def _load_yaml_file(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        """Load and parse YAML file with environment variable substitution."""
        file_path = Path(file_path)
        
        if not file_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {file_path}")
        
        try:
            content = file_path.read_text(encoding='utf-8')
            
            # Substitute environment variables
            content = self._substitute_env_vars(content)
            
            # Parse YAML
            data = yaml.safe_load(content)
            return data
            
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in {file_path}: {e}")
        except Exception as e:
            raise RuntimeError(f"Error loading {file_path}: {e}")
    
    def _substitute_env_vars(self, content: str) -> str:
        """Substitute environment variables in configuration content."""
        import re
        
        def replace_env_var(match):
            var_name = match.group(1)
            default_value = match.group(2) if match.group(2) else ""
            
            # Handle encrypted values
            env_value = os.getenv(var_name)
            if env_value and self.encryption.is_encrypted(env_value):
                try:
                    env_value = self.encryption.decrypt_value(env_value)
                except Exception as e:
                    logger.warning(f"Failed to decrypt environment variable {var_name}: {e}")
            
            return env_value or default_value
        
        # Replace ${VAR_NAME} and ${VAR_NAME:default_value}
        pattern = r'\$\{([A-Z_][A-Z0-9_]*):?([^}]*)\}'
        return re.sub(pattern, replace_env_var, content)
    
    def _load_env_file(self, env_file: Union[str, Path]) -> None:
        """Load environment variables from .env file."""
        env_file = Path(env_file)
        
        if not env_file.exists():
            logger.warning(f"Environment file not found: {env_file}")
            return
        
        try:
            for line in env_file.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip().strip('"\'')
                    
                    # Don't override existing environment variables
                    if key not in os.environ:
                        os.environ[key] = value
                        
            logger.debug(f"Loaded environment variables from {env_file}")
            
        except Exception as e:
            logger.error(f"Error loading environment file {env_file}: {e}")
    
    def _parse_hosts_config(self, data: Dict[str, Any]) -> List[HostConfig]:
        """Parse hosts configuration."""
        hosts = []
        
        for host_data in data.get('hosts', []):
            ssh_data = host_data.get('ssh', {})
            tmux_data = host_data.get('tmux', {})
            
            ssh_config = SSHConfig(
                host=ssh_data['host'],
                port=ssh_data.get('port', 22),
                user=ssh_data.get('user', 'root'),
                key_path=ssh_data.get('key'),
                password=ssh_data.get('password'),
                timeout=ssh_data.get('timeout', 30)
            )
            
            tmux_config = TmuxConfig(
                socket=tmux_data.get('socket', 'default'),
                session_filters=tmux_data.get('session_filters', []),
                pane_name_patterns=tmux_data.get('pane_name_patterns', []),
                capture_lines=tmux_data.get('capture_lines', 2000),
                poll_interval_ms=tmux_data.get('poll_interval_ms', 1500)
            )
            
            hosts.append(HostConfig(
                name=host_data['name'],
                ssh=ssh_config,
                tmux=tmux_config
            ))
        
        return hosts
    
    def _create_notification_config(self) -> NotificationConfig:
        """Create notification configuration from environment variables."""
        return NotificationConfig(
            channels=os.getenv("NOTIFICATION_CHANNELS", "stdout").split(","),
            serverchan_key=os.getenv("SERVERCHAN_API_KEY"),
            wechat_webhook=os.getenv("WECHAT_WORK_WEBHOOK"),
            email_smtp_server=os.getenv("SMTP_SERVER"),
            email_smtp_user=os.getenv("SMTP_USER"),
            email_smtp_password=os.getenv("SMTP_PASSWORD"),
            email_to=os.getenv("NOTIFICATION_EMAIL"),
            slack_webhook=os.getenv("SLACK_WEBHOOK_URL")
        )
    
    def _create_security_config(self) -> SecurityConfig:
        """Create security configuration from environment variables."""
        return SecurityConfig(
            approval_secret=os.getenv("APPROVAL_SECRET", "change-me-in-production"),
            approval_token_ttl=int(os.getenv("APPROVAL_TOKEN_TTL", "86400")),
            encrypt_config=os.getenv("ENCRYPT_CONFIG", "false").lower() == "true",
            encryption_key_file=os.getenv("ENCRYPTION_KEY_FILE")
        )
    
    def _create_database_config(self) -> DatabaseConfig:
        """Create database configuration from environment variables."""
        return DatabaseConfig(
            path=os.getenv("DATABASE_PATH", "~/.tmux_sentry/state.db"),
            backup_interval=int(os.getenv("DATABASE_BACKUP_INTERVAL", "300")),
            retention_days=int(os.getenv("DATABASE_RETENTION_DAYS", "30"))
        )
    
    def encrypt_sensitive_values(self, config_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt sensitive values in configuration."""
        sensitive_keys = {
            'password', 'secret', 'key', 'token', 'webhook', 'api_key'
        }
        
        def encrypt_recursive(obj):
            if isinstance(obj, dict):
                result = {}
                for key, value in obj.items():
                    if isinstance(value, str) and any(sensitive in key.lower() for sensitive in sensitive_keys):
                        if not self.encryption.is_encrypted(value):
                            result[key] = self.encryption.encrypt_value(value)
                        else:
                            result[key] = value
                    else:
                        result[key] = encrypt_recursive(value)
                return result
            elif isinstance(obj, list):
                return [encrypt_recursive(item) for item in obj]
            else:
                return obj
        
        return encrypt_recursive(config_dict)
    
    def save_config_template(self, output_file: Union[str, Path]) -> None:
        """Save a configuration template file."""
        template = {
            'hosts': [
                {
                    'name': 'example-host',
                    'ssh': {
                        'host': '${SSH_HOST:localhost}',
                        'port': '${SSH_PORT:22}',
                        'user': '${SSH_USER:root}',
                        'key': '${SSH_KEY_PATH:~/.ssh/id_rsa}'
                    },
                    'tmux': {
                        'socket': 'default',
                        'session_filters': ['^proj:'],
                        'pane_name_patterns': ['^codex', '^claude'],
                        'capture_lines': 2000,
                        'poll_interval_ms': 1500
                    }
                }
            ]
        }
        
        output_file = Path(output_file)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with output_file.open('w', encoding='utf-8') as f:
            yaml.dump(template, f, default_flow_style=False, indent=2)
        
        logger.info(f"Configuration template saved to {output_file}")
    
    def validate_config(self, config: Config) -> List[str]:
        """Validate configuration and return list of issues."""
        issues = []
        
        # Validate hosts
        if not config.hosts:
            issues.append("No hosts configured")
        
        for host in config.hosts:
            if not host.ssh.host:
                issues.append(f"Host {host.name}: SSH host not specified")
            
            if host.ssh.key_path:
                key_path = Path(host.ssh.key_path).expanduser()
                if not key_path.exists():
                    issues.append(f"Host {host.name}: SSH key file not found: {key_path}")
        
        # Validate pipelines
        if not config.policy.pipelines:
            issues.append("No pipelines configured")
        
        # Validate security
        if config.security.approval_secret == "change-me-in-production":
            issues.append("Approval secret should be changed from default value")
        
        return issues
#!/usr/bin/env python3

import os
import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

@dataclass
class DistributionConfig:
    name: str
    type: str  # 'apt' or 'yum'
    versions: List[str]
    mirror_urls: List[str]
    components: List[str] = None  # For APT repositories
    architectures: List[str] = None
    enabled: bool = True
    sync_schedule: str = "daily"

@dataclass
class MirrorConfig:
    base_path: str = "/srv/mirror"
    apt_path: str = "/srv/mirror/apt"
    yum_path: str = "/srv/mirror/yum"
    distributions: Dict[str, DistributionConfig] = None
    container_runtime: str = "docker"  # 'docker' or 'podman'
    max_concurrent_syncs: int = 3
    log_level: str = "INFO"
    
    def __post_init__(self):
        if self.distributions is None:
            self.distributions = self._get_default_distributions()
    
    def _get_default_distributions(self) -> Dict[str, DistributionConfig]:
        return {
            "debian": DistributionConfig(
                name="debian",
                type="apt",
                versions=["bullseye", "bookworm", "trixie"],
                mirror_urls=["http://deb.debian.org/debian/"],
                components=["main", "contrib", "non-free"],
                architectures=["amd64", "arm64"]
            ),
            "ubuntu": DistributionConfig(
                name="ubuntu",
                type="apt",
                versions=["focal", "jammy", "mantic", "noble"],
                mirror_urls=["http://archive.ubuntu.com/ubuntu/"],
                components=["main", "restricted", "universe", "multiverse"],
                architectures=["amd64", "arm64"]
            ),
            "kali": DistributionConfig(
                name="kali",
                type="apt",
                versions=["kali-rolling"],
                mirror_urls=["http://http.kali.org/kali/"],
                components=["main", "contrib", "non-free"],
                architectures=["amd64", "arm64"]
            ),
            "rocky": DistributionConfig(
                name="rocky",
                type="yum",
                versions=["8", "9"],
                mirror_urls=["https://download.rockylinux.org/pub/rocky/"],
                architectures=["x86_64", "aarch64"]
            ),
            "rhel": DistributionConfig(
                name="rhel",
                type="yum",
                versions=["8", "9"],
                mirror_urls=["https://cdn.redhat.com/content/dist/rhel/"],
                architectures=["x86_64", "aarch64"],
                enabled=False  # Requires subscription
            )
        }

class ConfigManager:
    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self._get_default_config_path()
        self._config: Optional[MirrorConfig] = None
    
    def _get_default_config_path(self) -> str:
        xdg_config = os.environ.get('XDG_CONFIG_HOME', '~/.config')
        return os.path.expanduser(f"{xdg_config}/linux-mirrors/config.yaml")
    
    def load_config(self) -> MirrorConfig:
        if self._config is not None:
            return self._config
        
        if not os.path.exists(self.config_path):
            self._config = MirrorConfig()
            self.save_config()
            return self._config
        
        try:
            with open(self.config_path, 'r') as f:
                data = yaml.safe_load(f)
            
            # Convert distribution configs
            distributions = {}
            if 'distributions' in data:
                for name, dist_data in data['distributions'].items():
                    distributions[name] = DistributionConfig(**dist_data)
                data['distributions'] = distributions
            
            self._config = MirrorConfig(**data)
            return self._config
            
        except Exception as e:
            raise ValueError(f"Error loading config from {self.config_path}: {e}")
    
    def save_config(self) -> None:
        if self._config is None:
            raise ValueError("No config loaded to save")
        
        # Ensure config directory exists
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        
        # Convert to dict for YAML serialization
        config_dict = asdict(self._config)
        
        with open(self.config_path, 'w') as f:
            yaml.dump(config_dict, f, default_flow_style=False, indent=2)
    
    def get_config(self) -> MirrorConfig:
        if self._config is None:
            return self.load_config()
        return self._config
    
    def update_distribution(self, name: str, config: DistributionConfig) -> None:
        if self._config is None:
            self.load_config()
        
        self._config.distributions[name] = config
        self.save_config()
    
    def get_enabled_distributions(self) -> Dict[str, DistributionConfig]:
        config = self.get_config()
        return {name: dist for name, dist in config.distributions.items() if dist.enabled}
    
    def get_distribution_path(self, dist_name: str) -> str:
        config = self.get_config()
        dist = config.distributions.get(dist_name)
        if not dist:
            raise ValueError(f"Unknown distribution: {dist_name}")
        
        if dist.type == "apt":
            return os.path.join(config.apt_path, dist_name)
        else:  # yum
            return os.path.join(config.yum_path, dist_name)
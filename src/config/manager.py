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
    # Air-gapped environment support
    include_gpg_keys: bool = True
    include_installer_images: bool = True
    include_source_packages: bool = False
    gpg_key_urls: List[str] = None
    installer_image_urls: List[str] = None
    # Proxy configuration (per-distribution)
    http_proxy: str = None
    https_proxy: str = None
    no_proxy: str = None

@dataclass
class MirrorConfig:
    base_path: str = None
    apt_path: str = None
    yum_path: str = None
    distributions: Dict[str, DistributionConfig] = None
    container_runtime: str = "podman"  # 'docker' or 'podman'
    max_concurrent_syncs: int = 3
    log_level: str = "INFO"
    # Air-gapped environment support
    nginx_config_path: str = None
    ssl_cert_path: str = None
    ssl_key_path: str = None
    generate_nginx_config: bool = True
    mirror_hostname: str = "mirror.local"
    # Global proxy configuration (applies to all distributions unless overridden)
    http_proxy: str = None
    https_proxy: str = None
    no_proxy: str = None
    enable_https: bool = False
    
    def __post_init__(self):
        if self.base_path is None:
            # Use user-accessible paths when not running as root
            if os.geteuid() == 0:
                self.base_path = "/srv/mirror"
            else:
                self.base_path = os.path.expanduser("~/mirrors")
        
        if self.apt_path is None:
            self.apt_path = os.path.join(self.base_path, "apt")
        
        if self.yum_path is None:
            self.yum_path = os.path.join(self.base_path, "yum")
        
        if self.nginx_config_path is None:
            self.nginx_config_path = os.path.join(self.base_path, "nginx")
            
        if self.distributions is None:
            self.distributions = self._get_default_distributions()
    
    def _get_default_distributions(self) -> Dict[str, DistributionConfig]:
        return {
            "debian": DistributionConfig(
                name="debian",
                type="apt",
                versions=["wheezy", "jessie", "stretch", "buster", "bullseye", "bookworm", "trixie"],
                mirror_urls=["http://deb.debian.org/debian/"],
                components=["main", "contrib", "non-free"],
                architectures=["amd64", "arm64", "i386", "armhf"],
                include_source_packages=True
            ),
            "ubuntu": DistributionConfig(
                name="ubuntu",
                type="apt",
                versions=["bionic", "focal", "jammy", "mantic", "noble", "oracular"],
                mirror_urls=["http://archive.ubuntu.com/ubuntu/"],
                components=["main", "restricted", "universe", "multiverse"],
                architectures=["amd64", "arm64", "i386", "armhf"],
                include_source_packages=True
            ),
            "kali": DistributionConfig(
                name="kali",
                type="apt",
                versions=["kali-rolling"],
                mirror_urls=["http://http.kali.org/kali/"],
                components=["main", "contrib", "non-free"],
                architectures=["amd64", "arm64", "i386", "armhf"],
                include_source_packages=True
            ),
            "rocky": DistributionConfig(
                name="rocky",
                type="yum",
                versions=["8", "9", "10"],
                mirror_urls=["https://dl.rockylinux.org/pub/rocky/"],
                components=["BaseOS", "AppStream", "PowerTools", "CRB", "extras", "devel", "plus", "HighAvailability", "ResilientStorage", "RT", "NFV", "SAP", "SAPHANA"],
                architectures=["x86_64"]
            ),
            "rhel": DistributionConfig(
                name="rhel",
                type="yum",
                versions=["8", "9", "10"],
                mirror_urls=["https://cdn.redhat.com/content/dist/rhel/"],
                components=["BaseOS", "AppStream", "PowerTools", "CRB", "extras", "devel", "plus", "HighAvailability", "ResilientStorage", "RT", "NFV", "SAP", "SAPHANA"],
                architectures=["x86_64"],
                enabled=True  # Enable RHEL in TUI (requires subscription)
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
            
            # Handle path defaults for user permissions
            if 'base_path' not in data or data['base_path'] == '/srv/mirror':
                # Force recalculation of paths based on current user permissions
                data.pop('base_path', None)
                data.pop('apt_path', None)
                data.pop('yum_path', None)
            
            self._config = MirrorConfig(**data)
            return self._config
            
        except Exception as e:
            raise ValueError(f"Error loading config from {self.config_path}: {e}")
    
    def save_config(self) -> None:
        if self._config is None:
            raise ValueError("No config loaded to save")
        
        # Ensure config directory exists
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        
        # Check if this is a new config (no proxy settings) - generate template
        if (not hasattr(self._config, 'http_proxy') or 
            (self._config.http_proxy is None and 
             self._config.https_proxy is None and 
             self._config.no_proxy is None)):
            self._create_config_template()
        else:
            # Standard YAML serialization for existing configs
            config_dict = asdict(self._config)
            with open(self.config_path, 'w') as f:
                yaml.dump(config_dict, f, default_flow_style=False, indent=2)
    
    def _create_config_template(self) -> None:
        """Create a new config file with commented proxy examples"""
        # Convert to dict for base structure
        config_dict = asdict(self._config)
        
        # Create template with commented proxy settings
        template = f"""# Linux Repository Mirror Configuration
# Generated by linux-mirrors

# Base storage paths
base_path: {config_dict['base_path']}
apt_path: {config_dict['apt_path']}
yum_path: {config_dict['yum_path']}

# Container runtime and concurrency
container_runtime: {config_dict['container_runtime']}
max_concurrent_syncs: {config_dict['max_concurrent_syncs']}
log_level: {config_dict['log_level']}

# Global proxy configuration (applies to all distributions unless overridden)
# Uncomment and configure if you need to use a proxy
# http_proxy: "http://proxy.company.com:8080"
# https_proxy: "http://proxy.company.com:8080"  
# no_proxy: "localhost,127.0.0.1,.local"

# Air-gapped environment support
nginx_config_path: {config_dict.get('nginx_config_path')}
ssl_cert_path: {config_dict.get('ssl_cert_path')}
ssl_key_path: {config_dict.get('ssl_key_path')}
generate_nginx_config: {str(config_dict.get('generate_nginx_config', True)).lower()}
mirror_hostname: {config_dict['mirror_hostname']}
enable_https: {str(config_dict.get('enable_https', False)).lower()}

# Distribution configurations
distributions:
"""
        
        # Add distributions
        for dist_name, dist_config in config_dict['distributions'].items():
            template += f"  {dist_name}:\n"
            template += f"    name: {dist_config['name']}\n"
            template += f"    type: {dist_config['type']}\n"
            template += f"    enabled: {str(dist_config['enabled']).lower()}\n"
            template += f"    versions:\n"
            for version in dist_config['versions']:
                template += f"    - '{version}'\n"
            template += f"    mirror_urls:\n"
            for url in dist_config['mirror_urls']:
                template += f"    - {url}\n"
            if dist_config.get('components'):
                template += f"    components:\n"
                for component in dist_config['components']:
                    template += f"    - {component}\n"
            if dist_config.get('architectures'):
                template += f"    architectures:\n"
                for arch in dist_config['architectures']:
                    template += f"    - {arch}\n"
            template += f"    sync_schedule: {dist_config['sync_schedule']}\n"
            template += f"    include_gpg_keys: {str(dist_config['include_gpg_keys']).lower()}\n"
            template += f"    include_installer_images: {str(dist_config['include_installer_images']).lower()}\n"
            template += f"    include_source_packages: {str(dist_config['include_source_packages']).lower()}\n"
            template += f"    # Per-distribution proxy overrides (uncomment if needed)\n"
            template += f"    # http_proxy: \"http://proxy.company.com:8080\"\n"
            template += f"    # https_proxy: \"http://proxy.company.com:8080\"\n"
            template += f"    # no_proxy: \"localhost,127.0.0.1,.local\"\n"
            template += f"\n"
        
        # Write template to file
        with open(self.config_path, 'w') as f:
            f.write(template)
    
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
#!/usr/bin/env python3

import os
import logging
from typing import Dict, List, Optional
from pathlib import Path
from ..config.manager import ConfigManager, DistributionConfig

logger = logging.getLogger(__name__)

class SystemdServiceGenerator:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.config = config_manager.get_config()
        self.service_dir = "/etc/systemd/system"
        self.user_service_dir = os.path.expanduser("~/.config/systemd/user")
    
    def generate_service_unit(self, dist_config: DistributionConfig, version: str, user_mode: bool = False) -> str:
        service_name = f"linux-mirror-{dist_config.name}-{version}"
        
        # Determine paths and user
        if user_mode:
            exec_start_pre = ""
            user_directive = ""
            working_directory = os.path.expanduser("~/mirrors")
        else:
            exec_start_pre = "ExecStartPre=/usr/bin/mkdir -p /srv/mirror"
            user_directive = "User=mirror\\nGroup=mirror"
            working_directory = "/srv/mirror"
        
        # Generate the main sync command
        sync_command = self._generate_sync_command(dist_config, version)
        
        service_content = f"""[Unit]
Description=Linux Repository Mirror - {dist_config.name.title()} {version}
Documentation=https://github.com/your-repo/linux-mirrors
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
{exec_start_pre}
ExecStart={sync_command}
ExecStartPost=/usr/bin/logger "Mirror sync completed for {dist_config.name} {version}"
{user_directive}
WorkingDirectory={working_directory}
Environment="PYTHONPATH=/usr/local/lib/python3/dist-packages"
Environment="CONTAINER_RUNTIME=docker"
TimeoutStartSec=3600
TimeoutStopSec=60
StandardOutput=journal
StandardError=journal
SyslogIdentifier={service_name}

# Resource limits
MemoryMax=2G
CPUQuota=50%

# Security settings
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={working_directory}
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
"""
        
        return service_content
    
    def generate_timer_unit(self, dist_config: DistributionConfig, version: str, schedule: str = "daily") -> str:
        service_name = f"linux-mirror-{dist_config.name}-{version}"
        timer_name = f"{service_name}.timer"
        
        # Convert schedule to systemd timer format
        on_calendar = self._schedule_to_systemd_calendar(schedule)
        
        timer_content = f"""[Unit]
Description=Timer for Linux Repository Mirror - {dist_config.name.title()} {version}
Documentation=https://github.com/your-repo/linux-mirrors
Requires={service_name}.service

[Timer]
OnCalendar={on_calendar}
RandomizedDelaySec=1800
Persistent=true
AccuracySec=1h

[Install]
WantedBy=timers.target
"""
        
        return timer_content
    
    def _generate_sync_command(self, dist_config: DistributionConfig, version: str) -> str:
        # This generates the command that will run the Python mirror script
        python_path = "/usr/bin/python3"  # or sys.executable
        script_path = "/usr/local/bin/linux-mirrors"
        
        command_parts = [
            python_path,
            script_path,
            "sync",
            "--distribution", dist_config.name,
            "--version", version,
            "--non-interactive"
        ]
        
        return " ".join(command_parts)
    
    def _schedule_to_systemd_calendar(self, schedule: str) -> str:
        schedule_mapping = {
            "hourly": "hourly",
            "daily": "daily",
            "weekly": "weekly",
            "monthly": "monthly",
            "twice-daily": "*-*-* 06,18:00:00",
            "every-6-hours": "*-*-* 00,06,12,18:00:00",
            "every-4-hours": "*-*-* 00,04,08,12,16,20:00:00"
        }
        
        return schedule_mapping.get(schedule, "daily")
    
    def create_service_files(self, dist_config: DistributionConfig, version: str, 
                           user_mode: bool = False, enable_timer: bool = True) -> Dict[str, str]:
        service_name = f"linux-mirror-{dist_config.name}-{version}"
        
        # Generate service and timer content
        service_content = self.generate_service_unit(dist_config, version, user_mode)
        timer_content = self.generate_timer_unit(dist_config, version, dist_config.sync_schedule)
        
        # Determine target directories
        target_dir = self.user_service_dir if user_mode else self.service_dir
        
        # Ensure target directory exists
        os.makedirs(target_dir, exist_ok=True)
        
        # Write service file
        service_file = os.path.join(target_dir, f"{service_name}.service")
        timer_file = os.path.join(target_dir, f"{service_name}.timer")
        
        try:
            with open(service_file, 'w') as f:
                f.write(service_content)
            logger.info(f"Created service file: {service_file}")
            
            if enable_timer:
                with open(timer_file, 'w') as f:
                    f.write(timer_content)
                logger.info(f"Created timer file: {timer_file}")
            
            return {
                'service_file': service_file,
                'timer_file': timer_file if enable_timer else None,
                'service_name': service_name
            }
            
        except PermissionError as e:
            error_msg = f"Permission denied creating service files. Try running with sudo or use --user mode."
            logger.error(error_msg)
            raise PermissionError(error_msg) from e
        except Exception as e:
            logger.error(f"Failed to create service files: {e}")
            raise
    
    def create_all_services(self, user_mode: bool = False, enable_timers: bool = True) -> List[Dict[str, str]]:
        created_services = []
        enabled_distributions = self.config_manager.get_enabled_distributions()
        
        for dist_name, dist_config in enabled_distributions.items():
            for version in dist_config.versions:
                try:
                    service_info = self.create_service_files(
                        dist_config, 
                        version, 
                        user_mode=user_mode,
                        enable_timer=enable_timers
                    )
                    service_info['distribution'] = dist_name
                    service_info['version'] = version
                    created_services.append(service_info)
                    
                except Exception as e:
                    logger.error(f"Failed to create service for {dist_name} {version}: {e}")
                    continue
        
        return created_services
    
    def generate_master_service(self, user_mode: bool = False) -> str:
        """Generate a master service that can sync all distributions"""
        service_name = "linux-mirror-all"
        
        if user_mode:
            working_directory = os.path.expanduser("~/mirrors")
            user_directive = ""
        else:
            working_directory = "/srv/mirror"
            user_directive = "User=mirror\\nGroup=mirror"
        
        sync_command = f"/usr/bin/python3 /usr/local/bin/linux-mirrors sync --all --non-interactive"
        
        service_content = f"""[Unit]
Description=Linux Repository Mirror - All Distributions
Documentation=https://github.com/your-repo/linux-mirrors
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=oneshot
ExecStartPre=/usr/bin/mkdir -p {working_directory}
ExecStart={sync_command}
ExecStartPost=/usr/bin/logger "All repository mirrors sync completed"
{user_directive}
WorkingDirectory={working_directory}
Environment="PYTHONPATH=/usr/local/lib/python3/dist-packages"
Environment="CONTAINER_RUNTIME=docker"
TimeoutStartSec=7200
TimeoutStopSec=120
StandardOutput=journal
StandardError=journal
SyslogIdentifier={service_name}

# Resource limits
MemoryMax=4G
CPUQuota=80%

# Security settings
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths={working_directory}
PrivateTmp=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
"""
        
        return service_content
    
    def enable_service(self, service_name: str, user_mode: bool = False) -> bool:
        """Enable a systemd service"""
        try:
            if user_mode:
                cmd = f"systemctl --user enable {service_name}"
            else:
                cmd = f"systemctl enable {service_name}"
            
            result = os.system(cmd)
            if result == 0:
                logger.info(f"Enabled service: {service_name}")
                return True
            else:
                logger.error(f"Failed to enable service: {service_name}")
                return False
                
        except Exception as e:
            logger.error(f"Error enabling service {service_name}: {e}")
            return False
    
    def start_timer(self, timer_name: str, user_mode: bool = False) -> bool:
        """Start and enable a systemd timer"""
        try:
            if user_mode:
                cmd = f"systemctl --user start {timer_name} && systemctl --user enable {timer_name}"
            else:
                cmd = f"systemctl start {timer_name} && systemctl enable {timer_name}"
            
            result = os.system(cmd)
            if result == 0:
                logger.info(f"Started and enabled timer: {timer_name}")
                return True
            else:
                logger.error(f"Failed to start timer: {timer_name}")
                return False
                
        except Exception as e:
            logger.error(f"Error starting timer {timer_name}: {e}")
            return False
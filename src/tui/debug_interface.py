#!/usr/bin/env python3

import asyncio
import logging
from typing import Dict, List, Optional, Any
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Button, Log, DataTable, Input, TextArea
from textual.reactive import reactive
from textual.screen import Screen

from config.manager import ConfigManager
from containers.orchestrator import ContainerOrchestrator
from storage.manager import StorageManager

logger = logging.getLogger(__name__)

class LogViewer(Container):
    def __init__(self, orchestrator: ContainerOrchestrator):
        super().__init__()
        self.orchestrator = orchestrator
        self.current_container_id: Optional[str] = None
    
    def compose(self) -> ComposeResult:
        yield Static("Container Logs", classes="section-header")
        
        with Horizontal():
            yield Input(placeholder="Container ID or name", id="container-input")
            yield Button("View Logs", id="view-logs", variant="primary")
            yield Button("Clear", id="clear-logs", variant="default")
        
        self.log_display = TextArea("", read_only=True, id="log-content")
        self.log_display.styles.height = "80%"
        yield self.log_display
    
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "view-logs":
            container_input = self.query_one("#container-input", Input)
            container_id = container_input.value.strip()
            if container_id:
                self.load_container_logs(container_id)
        elif event.button.id == "clear-logs":
            self.log_display.text = ""
    
    def load_container_logs(self, container_id: str):
        try:
            logs = self.orchestrator.get_container_logs(container_id, tail=1000)
            self.log_display.text = logs
            self.current_container_id = container_id
        except Exception as e:
            self.log_display.text = f"Error loading logs: {e}"

class ContainerManager(Container):
    def __init__(self, orchestrator: ContainerOrchestrator):
        super().__init__()
        self.orchestrator = orchestrator
    
    def compose(self) -> ComposeResult:
        yield Static("Container Management", classes="section-header")
        
        with Horizontal():
            yield Button("Refresh", id="refresh-containers", variant="default")
            yield Button("Cleanup Stopped", id="cleanup-containers", variant="warning")
            yield Button("Stop All", id="stop-all", variant="error")
        
        self.container_table = DataTable(id="debug-container-table")
        self.container_table.add_columns("ID", "Name", "Status", "Image", "Created")
        yield self.container_table
        
        with Horizontal():
            yield Input(placeholder="Container ID", id="container-action-input")
            yield Button("Stop", id="stop-container", variant="warning")
            yield Button("Inspect", id="inspect-container", variant="default")
    
    def on_mount(self):
        self.refresh_containers()
    
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "refresh-containers":
            self.refresh_containers()
        elif event.button.id == "cleanup-containers":
            self.cleanup_stopped_containers()
        elif event.button.id == "stop-all":
            self.stop_all_containers()
        elif event.button.id == "stop-container":
            self.stop_selected_container()
        elif event.button.id == "inspect-container":
            self.inspect_selected_container()
    
    def refresh_containers(self):
        try:
            containers = self.orchestrator.list_running_containers()
            
            # Clear and repopulate table
            self.container_table.clear()
            
            for container in containers:
                self.container_table.add_row(
                    container['id'],
                    container['name'],
                    container['status'],
                    container['image'],
                    container.get('created', 'N/A')[:19] if container.get('created') else 'N/A'
                )
            
            self.notify(f"Refreshed {len(containers)} containers")
            
        except Exception as e:
            self.notify(f"Failed to refresh containers: {e}", severity="error")
    
    def cleanup_stopped_containers(self):
        try:
            count = self.orchestrator.cleanup_stopped_containers()
            self.notify(f"Cleaned up {count} stopped containers", severity="success")
            self.refresh_containers()
        except Exception as e:
            self.notify(f"Failed to cleanup containers: {e}", severity="error")
    
    def stop_all_containers(self):
        try:
            # Get all running linux-mirror containers
            containers = self.orchestrator.list_running_containers()
            
            if not containers:
                self.notify("No running containers to stop", severity="info")
                return
            
            # Filter for only running containers (not exited ones)
            running_containers = [c for c in containers if c.get('status') in ['running', 'up']]
            
            if not running_containers:
                self.notify("No running containers found", severity="info")
                return
            
            stopped_count = 0
            failed_count = 0
            
            for container in running_containers:
                container_id = container.get('id')
                container_name = container.get('name', 'unknown')
                
                if container_id:
                    try:
                        self.orchestrator.stop_container(container_id)
                        stopped_count += 1
                        logger.info(f"Stopped container {container_name}")
                    except Exception as e:
                        failed_count += 1
                        logger.error(f"Failed to stop container {container_name}: {e}")
            
            # Show results
            if stopped_count > 0:
                self.notify(f"Stopped {stopped_count} containers" + 
                           (f", {failed_count} failed" if failed_count > 0 else ""), 
                           severity="success" if failed_count == 0 else "warning")
            else:
                self.notify(f"Failed to stop all containers", severity="error")
            
            # Refresh the container list
            self.refresh_containers()
            
        except Exception as e:
            logger.error(f"Error in stop_all_containers: {e}")
            self.notify(f"Failed to stop containers: {e}", severity="error")
    
    def stop_selected_container(self):
        container_input = self.query_one("#container-action-input", Input)
        container_id = container_input.value.strip()
        
        if not container_id:
            self.notify("Please enter a container ID", severity="error")
            return
        
        try:
            self.orchestrator.stop_container(container_id)
            self.notify(f"Stopped container {container_id}", severity="success")
            self.refresh_containers()
        except Exception as e:
            self.notify(f"Failed to stop container: {e}", severity="error")
    
    def inspect_selected_container(self):
        container_input = self.query_one("#container-action-input", Input)
        container_id = container_input.value.strip()
        
        if not container_id:
            self.notify("Please enter a container ID", severity="error")
            return
        
        try:
            status = self.orchestrator.get_container_status(container_id)
            
            # Display status information
            info_lines = [
                f"ID: {status.get('id', 'N/A')}",
                f"Name: {status.get('name', 'N/A')}",
                f"Status: {status.get('status', 'N/A')}",
                f"Image: {status.get('image', 'N/A')}",
                f"Created: {status.get('created', 'N/A')}",
                f"Started: {status.get('started', 'N/A')}",
                f"Finished: {status.get('finished', 'N/A')}"
            ]
            
            self.notify("\\n".join(info_lines), severity="info")
            
        except Exception as e:
            self.notify(f"Failed to inspect container: {e}", severity="error")

class StorageInfo(Container):
    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config_manager = config_manager
        self.storage_manager = StorageManager(config_manager)
    
    def compose(self) -> ComposeResult:
        yield Static("Storage Information", classes="section-header")
        
        with Horizontal():
            yield Button("Refresh", id="refresh-storage", variant="default")
            yield Button("Cleanup", id="cleanup-storage", variant="warning")
        
        self.storage_table = DataTable(id="storage-table")
        self.storage_table.add_columns("Path", "Size", "Used %", "Free Space")
        yield self.storage_table
        
        yield Static("", id="storage-summary")
    
    def on_mount(self):
        self.refresh_storage_info()
    
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "refresh-storage":
            self.refresh_storage_info()
        elif event.button.id == "cleanup-storage":
            self.cleanup_storage()
    
    def refresh_storage_info(self):
        try:
            storage_info = self.storage_manager.get_storage_info()
            
            # Clear and repopulate table
            self.storage_table.clear()
            
            for path_info in storage_info['paths']:
                self.storage_table.add_row(
                    path_info['path'],
                    self._format_size(path_info['total_size']),
                    f"{path_info['used_percent']:.1f}%",
                    self._format_size(path_info['free_space'])
                )
            
            # Update summary
            summary = self.query_one("#storage-summary", Static)
            summary.update(f"Total repositories: {storage_info['total_repos']}")
            
            self.notify("Storage information refreshed")
            
        except Exception as e:
            self.notify(f"Failed to get storage info: {e}", severity="error")
    
    def cleanup_storage(self):
        try:
            result = self.storage_manager.cleanup_old_syncs()
            self.notify(f"Cleanup completed: freed {self._format_size(result['freed_space'])}", 
                       severity="success")
            self.refresh_storage_info()
        except Exception as e:
            self.notify(f"Cleanup failed: {e}", severity="error")
    
    def _format_size(self, size_bytes: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"

class DebugInterface(Screen):
    CSS_PATH = "debug_interface.css"
    
    def __init__(self):
        super().__init__()
        self.config_manager = ConfigManager()
        self.orchestrator = ContainerOrchestrator(self.config_manager)
    
    def compose(self) -> ComposeResult:
        yield Header()
        
        with Container(id="debug-container"):
            with Horizontal(id="debug-content"):
                with Vertical(id="debug-left-panel"):
                    yield ContainerManager(self.orchestrator)
                    yield StorageInfo(self.config_manager)
                
                with Vertical(id="debug-right-panel"):
                    yield LogViewer(self.orchestrator)
            
            with Horizontal(id="debug-actions"):
                yield Button("Back to Main", id="back-main", variant="primary")
                yield Button("Export Logs", id="export-logs", variant="default")
                yield Button("System Info", id="system-info", variant="default")
        
        yield Footer()
    
    def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "back-main":
            self.dismiss()
        elif event.button.id == "export-logs":
            self.export_debug_logs()
        elif event.button.id == "system-info":
            self.show_system_info()
    
    def export_debug_logs(self):
        # This would export all container logs and system information
        self.notify("Export logs - not implemented yet", severity="info")
    
    def show_system_info(self):
        try:
            import platform
            import psutil
            
            info_lines = [
                f"System: {platform.system()} {platform.release()}",
                f"Python: {platform.python_version()}",
                f"CPU Usage: {psutil.cpu_percent()}%",
                f"Memory Usage: {psutil.virtual_memory().percent}%",
                f"Disk Usage: {psutil.disk_usage('/').percent}%"
            ]
            
            self.notify("\\n".join(info_lines), severity="info")
            
        except Exception as e:
            self.notify(f"Failed to get system info: {e}", severity="error")
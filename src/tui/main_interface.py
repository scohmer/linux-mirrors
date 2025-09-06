#!/usr/bin/env python3

import asyncio
from typing import Dict, List, Optional, Any
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Button, Checkbox, Log, ProgressBar, DataTable
from textual.reactive import reactive
from textual.message import Message
from rich.text import Text
from rich.console import Console

from config.manager import ConfigManager, DistributionConfig
from containers.orchestrator import ContainerOrchestrator
from sync.engines import SyncManager
from .debug_interface import DebugInterface

class SyncProgress(Static):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.progress_data: Dict[str, Dict[str, Any]] = {}
    
    def update_progress(self, dist_name: str, version: str, status: str, details: str = ""):
        key = f"{dist_name}-{version}"
        self.progress_data[key] = {
            'distribution': dist_name,
            'version': version,
            'status': status,
            'details': details
        }
        self._update_display()
    
    def _update_display(self):
        content = []
        for key, data in self.progress_data.items():
            status_color = {
                'pending': 'yellow',
                'running': 'blue',
                'completed': 'green',
                'failed': 'red'
            }.get(data['status'], 'white')
            
            line = f"[{status_color}]{data['distribution']} {data['version']}: {data['status']}[/]"
            if data['details']:
                line += f" - {data['details']}"
            content.append(line)
        
        self.update("\n".join(content))

class DistributionSelector(Container):
    def __init__(self, config_manager: ConfigManager):
        super().__init__()
        self.config_manager = config_manager
        self.config = config_manager.get_config()
        self.checkboxes: Dict[str, Dict[str, Checkbox]] = {}
    
    def compose(self) -> ComposeResult:
        yield Static("Select distributions and versions to sync:", classes="section-header")
        
        for dist_name, dist_config in self.config.distributions.items():
            if not dist_config.enabled:
                continue
                
            with Container(classes="distribution-container"):
                yield Static(f"{dist_name.title()} ({dist_config.type.upper()})", classes="dist-title")
                
                # Version checkboxes
                self.checkboxes[dist_name] = {}
                for version in dist_config.versions:
                    checkbox = Checkbox(f"  {version}", value=False, id=f"{dist_name}-{version}")
                    self.checkboxes[dist_name][version] = checkbox
                    yield checkbox
    
    def get_selected_distributions(self) -> Dict[str, List[str]]:
        selected = {}
        for dist_name, version_checkboxes in self.checkboxes.items():
            selected_versions = []
            for version, checkbox in version_checkboxes.items():
                if checkbox.value:
                    selected_versions.append(version)
            if selected_versions:
                selected[dist_name] = selected_versions
        return selected
    
    def select_all_distributions(self):
        for dist_checkboxes in self.checkboxes.values():
            for checkbox in dist_checkboxes.values():
                checkbox.value = True
    
    def clear_all_selections(self):
        for dist_checkboxes in self.checkboxes.values():
            for checkbox in dist_checkboxes.values():
                checkbox.value = False

class MainInterface(App):
    CSS_PATH = "main_interface.css"
    TITLE = "Linux Repository Mirror Manager"
    SUB_TITLE = "Containerized repository synchronization"
    
    def __init__(self):
        super().__init__()
        self.config_manager = ConfigManager()
        self.orchestrator = ContainerOrchestrator(self.config_manager)
        self.sync_manager = SyncManager(self.orchestrator)
        self.is_syncing = reactive(False)
        self.sync_results: List[Dict[str, Any]] = []
    
    def compose(self) -> ComposeResult:
        yield Header()
        
        with Container(id="main-container"):
            with Horizontal(id="content-area"):
                with Vertical(id="left-panel"):
                    self.selector = DistributionSelector(self.config_manager)
                    yield self.selector
                    
                    with Horizontal(id="button-row"):
                        yield Button("Select All", id="select-all", variant="default")
                        yield Button("Clear All", id="clear-all", variant="default") 
                        yield Button("Start Sync", id="start-sync", variant="primary")
                        yield Button("Debug Menu", id="debug-menu", variant="warning")
                
                with Vertical(id="right-panel"):
                    yield Static("Sync Progress", classes="section-header")
                    self.progress = SyncProgress(id="sync-progress")
                    yield self.progress
                    
                    yield Static("Container Status", classes="section-header")
                    self.container_table = DataTable(id="container-table")
                    self.container_table.add_columns("Container", "Status", "Image")
                    yield self.container_table
        
        yield Footer()
    
    def on_mount(self):
        self.set_interval(5.0, self.update_container_status)
        self.install_screen(DebugInterface(), name="debug")
    
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "select-all":
            self.selector.select_all_distributions()
        elif event.button.id == "clear-all":
            self.selector.clear_all_selections()
        elif event.button.id == "start-sync":
            self.start_sync_process()
        elif event.button.id == "debug-menu":
            self.push_screen("debug")
    
    def start_sync_process(self):
        if self.is_syncing:
            self.notify("Sync already in progress", severity="warning")
            return
        
        selected = self.selector.get_selected_distributions()
        if not selected:
            self.notify("No distributions selected", severity="error")
            return
        
        self.is_syncing = True
        asyncio.create_task(self._run_sync(selected))
    
    async def _run_sync(self, selected_distributions: Dict[str, List[str]]):
        try:
            self.notify("Starting repository synchronization", severity="info")
            
            # Prepare sync tasks
            sync_tasks = []
            for dist_name, versions in selected_distributions.items():
                dist_config = self.config_manager.get_config().distributions[dist_name]
                
                # Update progress for each version
                for version in versions:
                    self.progress.update_progress(dist_name, version, "pending")
                
                # Create sync task
                task = self.sync_manager.sync_distribution(dist_config, versions)
                sync_tasks.append(task)
            
            # Execute all sync tasks
            all_results = []
            for task in sync_tasks:
                results = await task
                all_results.extend(results)
                
                # Update progress for completed syncs
                for result in results:
                    status = result.get('status', 'unknown')
                    error = result.get('error', '')
                    self.progress.update_progress(
                        result['distribution'],
                        result['version'],
                        status,
                        error if status == 'failed' else ''
                    )
            
            self.sync_results = all_results
            
            # Show completion notification
            successful = len([r for r in all_results if r.get('status') == 'completed'])
            total = len(all_results)
            self.notify(f"Sync completed: {successful}/{total} successful", 
                       severity="success" if successful == total else "warning")
            
        except Exception as e:
            self.notify(f"Sync failed: {e}", severity="error")
            
        finally:
            self.is_syncing = False
    
    def update_container_status(self):
        containers = self.orchestrator.list_running_containers()
        
        # Clear existing rows
        self.container_table.clear()
        
        # Add current containers
        for container in containers:
            self.container_table.add_row(
                container['name'],
                container['status'],
                container['image']
            )
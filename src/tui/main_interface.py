#!/usr/bin/env python3

import asyncio
import logging
from typing import Dict, List, Optional, Any
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Button, Switch, Log, ProgressBar, DataTable
from textual.reactive import reactive
from textual.message import Message
from rich.text import Text
from rich.console import Console

from config.manager import ConfigManager, DistributionConfig
from containers.orchestrator import ContainerOrchestrator
from sync.engines import SyncManager
from .debug_interface import DebugInterface

logger = logging.getLogger(__name__)

class SyncProgress(Static):
    def __init__(self, *args, **kwargs):
        super().__init__("No sync in progress", *args, **kwargs)
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
        self.switches: Dict[str, Dict[str, Switch]] = {}
    
    def compose(self) -> ComposeResult:
        yield Static("Select distributions and versions to sync:", classes="section-header")
        yield Static("Use Tab/Shift+Tab to navigate, Space/Enter to toggle switches", id="instructions")
        
        for dist_name, dist_config in self.config.distributions.items():
            if not dist_config.enabled:
                continue
            
            yield Static(f"{dist_name.title()} ({dist_config.type.upper()})", classes="dist-title")
            
            # Create horizontal layout for versions
            with Horizontal(classes="version-row"):
                self.switches[dist_name] = {}
                for version in dist_config.versions:
                    # Create a simple vertical group for each version
                    with Vertical(classes="version-item"):
                        yield Static(f"{version}", classes="version-label")
                        switch = Switch(value=False, id=f"{dist_name}-{version}")
                        switch.can_focus = True
                        self.switches[dist_name][version] = switch
                        yield switch
    
    def get_selected_distributions(self) -> Dict[str, List[str]]:
        selected = {}
        for dist_name, version_switches in self.switches.items():
            selected_versions = []
            for version, switch in version_switches.items():
                if switch.value:
                    selected_versions.append(version)
            if selected_versions:
                selected[dist_name] = selected_versions
        return selected
    
    def select_all_distributions(self):
        for dist_switches in self.switches.values():
            for switch in dist_switches.values():
                switch.value = True
    
    def clear_all_selections(self):
        for dist_switches in self.switches.values():
            for switch in dist_switches.values():
                switch.value = False

class MainInterface(App):
    CSS_PATH = "main_interface.css"
    TITLE = "Linux Repository Mirror Manager"
    SUB_TITLE = "Containerized repository synchronization"
    
    BINDINGS = [
        ("ctrl+a", "select_all", "Select All"),
        ("ctrl+n", "clear_all", "Clear All"),
        ("ctrl+s", "start_sync", "Start Sync"),
        ("ctrl+d", "debug_menu", "Debug Menu"),
        ("ctrl+r", "reset_sync", "Reset Sync State"),
        ("ctrl+q", "quit", "Quit"),
    ]
    
    def __init__(self):
        super().__init__()
        self.config_manager = ConfigManager()
        self.orchestrator = ContainerOrchestrator(self.config_manager)
        self.sync_manager = SyncManager(self.orchestrator)
        self.is_syncing = reactive(False)
        self.sync_results: List[Dict[str, Any]] = []
        self._current_sync_task: Optional[asyncio.Task] = None
    
    def compose(self) -> ComposeResult:
        yield Header()
        
        with Container(id="main-container"):
            with Horizontal(id="content-area"):
                with Vertical(id="left-panel"):
                    self.selector = DistributionSelector(self.config_manager)
                    yield self.selector
                
                with Vertical(id="right-panel"):
                    yield Static("Sync Progress", classes="section-header")
                    self.progress = SyncProgress(id="sync-progress")
                    yield self.progress
                    
                    yield Static("Container Status", classes="section-header")
                    self.container_table = DataTable(id="container-table")
                    self.container_table.add_columns("Container", "Status", "Image")
                    yield self.container_table
                    
                    yield Static("Actions", classes="section-header")
                    with Horizontal(id="button-row"):
                        yield Button("Select All", id="select-all", variant="default")
                        yield Button("Clear All", id="clear-all", variant="default") 
                        yield Button("Start Sync", id="start-sync", variant="primary")
                        yield Button("Debug Menu", id="debug-menu", variant="warning")
        
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
    
    def action_select_all(self):
        """Select all distributions via keyboard shortcut"""
        self.selector.select_all_distributions()
    
    def action_clear_all(self):
        """Clear all selections via keyboard shortcut"""
        self.selector.clear_all_selections()
    
    def action_start_sync(self):
        """Start sync via keyboard shortcut"""
        self.start_sync_process()
    
    def action_debug_menu(self):
        """Open debug menu via keyboard shortcut"""
        self.push_screen("debug")
    
    def action_reset_sync(self):
        """Reset sync state manually - useful for debugging"""
        self.is_syncing = False
        if hasattr(self, '_current_sync_task') and self._current_sync_task:
            if not self._current_sync_task.done():
                self._current_sync_task.cancel()
        self._current_sync_task = None
        self.notify("Sync state reset", severity="info")
    
    def start_sync_process(self):
        if self.is_syncing:
            self.notify("Sync already in progress", severity="warning")
            return
        
        selected = self.selector.get_selected_distributions()
        if not selected:
            self.notify("No distributions selected", severity="error")
            return
        
        self.is_syncing = True
        # Create and store the sync task so it doesn't get garbage collected
        sync_task = asyncio.create_task(self._run_sync(selected))
        # Store task reference to prevent garbage collection
        self._current_sync_task = sync_task
    
    async def _run_sync(self, selected_distributions: Dict[str, List[str]]):
        try:
            self.notify("Starting repository synchronization", severity="info")
            logger.info(f"TUI sync starting for: {selected_distributions}")
            
            # Update progress for all selected versions
            for dist_name, versions in selected_distributions.items():
                for version in versions:
                    self.progress.update_progress(dist_name, version, "pending")
            
            # Use the new sync_multiple_distributions method for proper concurrency control
            logger.info(f"Starting multi-distribution sync for: {selected_distributions}")
            all_results = await self.sync_manager.sync_multiple_distributions(selected_distributions)
            logger.info(f"Multi-distribution sync completed with {len(all_results)} results")
            
            # Update progress for completed syncs
            for result in all_results:
                status = result.get('status', 'unknown')
                error = result.get('error', '')
                logger.info(f"Result: {result.get('distribution')} {result.get('version')} = {status}")
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
            logger.info(f"Sync completed: {successful}/{total} successful")
            self.notify(f"Sync completed: {successful}/{total} successful", 
                       severity="success" if successful == total else "warning")
            
        except Exception as e:
            logger.error(f"TUI sync failed: {e}", exc_info=True)
            self.notify(f"Sync failed: {e}", severity="error")
            
        finally:
            logger.info("TUI sync finished, resetting is_syncing flag")
            self.is_syncing = False
            self._current_sync_task = None
    
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
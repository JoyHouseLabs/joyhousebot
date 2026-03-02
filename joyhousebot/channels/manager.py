"""Channel manager for coordinating chat channels using the plugin system."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from joyhousebot.bus.events import OutboundMessage
from joyhousebot.bus.queue import MessageBus
from joyhousebot.channels.plugins import get_channel_registry, ChannelPlugin
from joyhousebot.config.schema import Config
from joyhousebot.utils.exceptions import (
    ChannelError,
    sanitize_error_message,
    classify_exception,
)


class ChannelManager:
    """
    Manages chat channels using the plugin system.
    
    Responsibilities:
    - Load and register channel plugins
    - Start/stop channel plugins
    - Route outbound messages
    """
    
    def __init__(self, config: Config, bus: MessageBus):
        self.config = config
        self.bus = bus
        self.plugins: dict[str, ChannelPlugin] = {}
        self._dispatch_task: asyncio.Task | None = None
        
        self._init_channels()
    
    def _get_channel_config(self, channel_id: str) -> dict[str, Any]:
        """Extract channel config from the Config object as a dict."""
        channel_configs = {
            "telegram": self.config.channels.telegram,
            "discord": self.config.channels.discord,
            "slack": self.config.channels.slack,
            "whatsapp": self.config.channels.whatsapp,
            "feishu": self.config.channels.feishu,
            "dingtalk": self.config.channels.dingtalk,
            "mochat": self.config.channels.mochat,
            "email": self.config.channels.email,
            "qq": self.config.channels.qq,
        }
        
        cfg = channel_configs.get(channel_id)
        if cfg is None:
            return {}
        
        return cfg.model_dump()
    
    def _is_channel_enabled(self, channel_id: str) -> bool:
        """Check if a channel is enabled in config."""
        enabled_map = {
            "telegram": self.config.channels.telegram.enabled,
            "discord": self.config.channels.discord.enabled,
            "slack": self.config.channels.slack.enabled,
            "whatsapp": self.config.channels.whatsapp.enabled,
            "feishu": self.config.channels.feishu.enabled,
            "dingtalk": self.config.channels.dingtalk.enabled,
            "mochat": self.config.channels.mochat.enabled,
            "email": self.config.channels.email.enabled,
            "qq": self.config.channels.qq.enabled,
        }
        return enabled_map.get(channel_id, False)
    
    def _init_channels(self) -> None:
        """Initialize channel plugins based on config."""
        registry = get_channel_registry()
        registry.load_all_builtins()
        
        plugins_dir = getattr(self.config, "plugins_dir", None)
        if plugins_dir:
            from pathlib import Path
            plugins_path = Path(plugins_dir).expanduser()
            if plugins_path.exists():
                external = registry.load_from_directory(plugins_path / "channels")
                if external:
                    logger.info(f"Loaded {len(external)} external channel plugins from {plugins_path}")
        
        messages_config = getattr(self.config, "messages", None)
        commands_config = getattr(self.config, "commands", None)
        
        for channel_id in registry.list_channels():
            if not self._is_channel_enabled(channel_id):
                continue
            
            try:
                plugin = registry.get(channel_id)
                if plugin is None:
                    logger.warning(f"Channel plugin {channel_id} not found in registry")
                    continue
                
                config_dict = self._get_channel_config(channel_id)
                
                if channel_id == "telegram":
                    config_dict["groq_api_key"] = self.config.providers.groq.api_key
                config_dict["messages_config"] = messages_config
                config_dict["commands_config"] = commands_config
                
                plugin.configure(config_dict, self.bus)
                self.plugins[channel_id] = plugin
                logger.info(f"{channel_id} channel enabled (plugin)")
                
            except ImportError as e:
                logger.warning(f"Channel plugin {channel_id} not available: {e}")
            except Exception as e:
                logger.error(f"Failed to initialize channel {channel_id}: {e}")
    
    async def _start_plugin(self, name: str, plugin: ChannelPlugin) -> None:
        """Start a channel plugin and log any exceptions."""
        try:
            await plugin.start()
        except ChannelError as e:
            logger.error(f"Channel {name} error [{e.code}]: {e.message}")
        except asyncio.TimeoutError:
            logger.error(f"Channel {name}: connection timed out")
        except ConnectionError as e:
            logger.error(f"Channel {name}: connection failed - {sanitize_error_message(str(e))}")
        except PermissionError as e:
            logger.error(f"Channel {name}: permission denied - {sanitize_error_message(str(e))}")
        except Exception as e:
            code, category, _ = classify_exception(e)
            logger.error(f"Failed to start channel {name} [{code}]: {sanitize_error_message(str(e))}")

    async def start_all(self) -> None:
        """Start all channel plugins and the outbound dispatcher."""
        if not self.plugins:
            logger.warning("No channels enabled")
            return
        
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())
        
        tasks = []
        for name, plugin in self.plugins.items():
            logger.info(f"Starting {name} channel...")
            tasks.append(asyncio.create_task(self._start_plugin(name, plugin)))
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def stop_all(self) -> None:
        """Stop all channel plugins and the dispatcher."""
        logger.info("Stopping all channels...")
        
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass
        
        for name, plugin in self.plugins.items():
            try:
                await plugin.stop()
                logger.info(f"Stopped {name} channel")
            except ChannelError as e:
                logger.error(f"Channel {name} stop error [{e.code}]: {e.message}")
            except asyncio.TimeoutError:
                logger.error(f"Channel {name}: stop timed out")
            except Exception as e:
                code, _, _ = classify_exception(e)
                logger.error(f"Error stopping {name} [{code}]: {sanitize_error_message(str(e))}")
    
    async def _dispatch_outbound(self) -> None:
        """Dispatch outbound messages to the appropriate channel plugin."""
        logger.info("Outbound dispatcher started")
        
        while True:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_outbound(),
                    timeout=1.0
                )
                
                plugin = self.plugins.get(msg.channel)
                if plugin:
                    try:
                        result = await plugin.send(msg)
                        if not result.success:
                            logger.warning(f"Failed to send to {msg.channel}: {result.error}")
                    except ChannelError as e:
                        logger.error(f"Channel {msg.channel} send error [{e.code}]: {e.message}")
                    except asyncio.TimeoutError:
                        logger.error(f"Channel {msg.channel}: send timed out")
                    except ConnectionError as e:
                        logger.error(f"Channel {msg.channel}: connection lost - {sanitize_error_message(str(e))}")
                    except Exception as e:
                        code, _, _ = classify_exception(e)
                        logger.error(f"Error sending to {msg.channel} [{code}]: {sanitize_error_message(str(e))}")
                else:
                    logger.warning(f"Unknown channel: {msg.channel}")
                    
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
    
    def get_channel(self, name: str) -> ChannelPlugin | None:
        """Get a channel plugin by name."""
        return self.plugins.get(name)
    
    def get_status(self) -> dict[str, Any]:
        """Get status of all channels."""
        return {
            name: {
                "enabled": True,
                "running": plugin.is_running
            }
            for name, plugin in self.plugins.items()
        }
    
    @property
    def enabled_channels(self) -> list[str]:
        """Get list of enabled channel names."""
        return list(self.plugins.keys())

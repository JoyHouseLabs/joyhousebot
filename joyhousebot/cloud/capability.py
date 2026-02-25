"""能力介绍卡生成器"""

from __future__ import annotations

from typing import Any
from pathlib import Path

from joyhousebot.config.schema import CapabilityItem, CloudConnectConfig
from joyhousebot.cloud.auth import CloudAuth


class CapabilityCardGenerator:
    """能力介绍卡生成器"""

    def __init__(self, config: CloudConnectConfig, auth: CloudAuth):
        self.config = config
        self.auth = auth

    def generate(self) -> dict[str, Any]:
        """生成能力介绍卡"""
        enabled_capabilities = [
            cap for cap in self.config.capabilities if cap.enabled
        ]

        return {
            "version": "1.0",
            "node_id": self.auth.house_id or "unknown",
            "node_name": self.config.house_name or "Joyhousebot Node",
            "description": self.config.description or "基于 Claude 的智能助手",
            "public_key": self.auth.public_key_hex,
            "capabilities": [
                self._capability_to_dict(cap) for cap in enabled_capabilities
            ],
            "auth_method": "ed25519",
            "endpoints": {
                "task_create": "/api/v1/house_tasks",
                "task_query": "/api/v1/house_tasks/{task_id}",
            },
        }

    def _capability_to_dict(self, cap: CapabilityItem) -> dict[str, Any]:
        """将能力项转换为字典"""
        return {
            "id": cap.id,
            "name": cap.name,
            "description": cap.description,
            "version": cap.version,
        }

    def generate_full_schema(self) -> dict[str, Any]:
        """生成包含参数 schema 的完整能力卡"""
        card = self.generate()

        capability_schemas = {
            "chat.v1": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "用户消息"},
                    "session_id": {"type": "string", "description": "会话ID，可选"},
                },
                "required": ["message"],
            },
            "code_execution.v1": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "description": "编程语言"},
                    "code": {"type": "string", "description": "要执行的代码"},
                    "timeout": {"type": "integer", "description": "超时时间（秒）"},
                },
                "required": ["language", "code"],
            },
            "file_operations.v1": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["read", "write", "delete", "list"],
                        "description": "操作类型",
                    },
                    "path": {"type": "string", "description": "文件路径"},
                    "content": {"type": "string", "description": "文件内容（写入时）"},
                },
                "required": ["operation", "path"],
            },
            "web_search.v1": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索查询"},
                    "num_results": {"type": "integer", "description": "结果数量", "default": 5},
                },
                "required": ["query"],
            },
        }

        for cap in card["capabilities"]:
            if cap["id"] in capability_schemas:
                cap["params_schema"] = capability_schemas[cap["id"]]

        return card

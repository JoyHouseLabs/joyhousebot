"""Tests for services.skills.skill_service."""

from pathlib import Path

from joyhousebot.config.schema import Config
from joyhousebot.services.skills.skill_service import (
    build_skills_status_report,
    list_skills_for_api,
    patch_skill_enabled,
)


def test_build_skills_status_report():
    cfg = Config()
    report = build_skills_status_report(cfg)
    assert "workspaceDir" in report
    assert "managedSkillsDir" in report
    assert "skills" in report
    assert isinstance(report["skills"], list)


def test_list_skills_for_api():
    cfg = Config()
    out = list_skills_for_api(cfg)
    assert isinstance(out, list)
    for row in out:
        assert "name" in row
        assert "source" in row
        assert "description" in row
        assert "available" in row
        assert "enabled" in row


def test_patch_skill_enabled():
    cfg = Config()
    app_state = {}
    patch_skill_enabled(
        config=cfg,
        name="test.skill",
        enabled=False,
        save_config=lambda c: None,
        app_state=app_state,
    )
    assert cfg.skills.entries is not None
    assert "test.skill" in cfg.skills.entries
    assert cfg.skills.entries["test.skill"].enabled is False

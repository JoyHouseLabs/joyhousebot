"""Feedback loop for decision verification and learning."""

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.collaboration.types import (
    CollaborationRequest,
    CollaborationResult,
    FeedbackRecord,
)


class FeedbackLoop:
    """Manages feedback collection and decision verification."""
    
    def __init__(
        self,
        workspace: Path,
        reminder_days: int = 7,
    ):
        self.workspace = workspace
        self.feedback_dir = workspace / "collaboration" / "feedback"
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self.decisions_file = self.feedback_dir / "decisions.jsonl"
        self.verifications_file = self.feedback_dir / "verifications.jsonl"
        self.reminder_days = reminder_days
    
    async def record(
        self,
        request: CollaborationRequest,
        result: CollaborationResult,
    ) -> FeedbackRecord:
        """
        Record a decision for later verification.
        
        Args:
            request: The original request
            result: The decision result
            
        Returns:
            FeedbackRecord with reminder time
        """
        record = FeedbackRecord(
            trace_id=request.request_id,
            goal=request.goal,
            decision=result.decision,
            confidence=result.confidence,
            reasoning=result.reasoning,
            context=request.context,
            reminder_at=datetime.now() + timedelta(days=self.reminder_days),
        )
        
        self._append_record(self.decisions_file, record.model_dump())
        
        logger.info(f"Recorded decision for verification: {record.feedback_id}")
        return record
    
    async def verify(
        self,
        feedback_id: str,
        actual_outcome: str,
        correct: bool,
        notes: str | None = None,
    ) -> FeedbackRecord | None:
        """
        Verify a decision with actual outcome.
        
        Args:
            feedback_id: The feedback record ID
            actual_outcome: What actually happened
            correct: Whether the decision was correct
            notes: Optional notes
            
        Returns:
            Updated FeedbackRecord or None if not found
        """
        records = self._load_records(self.decisions_file)
        
        for record_data in records:
            if record_data.get("feedback_id") == feedback_id:
                record = FeedbackRecord.model_validate(record_data)
                record.verify(actual_outcome, correct, notes)
                
                self._update_record(
                    self.decisions_file,
                    "feedback_id",
                    feedback_id,
                    record.model_dump(),
                )
                
                self._append_record(self.verifications_file, {
                    "feedback_id": feedback_id,
                    "actual_outcome": actual_outcome,
                    "correct": correct,
                    "notes": notes,
                    "verified_at": record.verified_at.isoformat() if record.verified_at else None,
                })
                
                logger.info(f"Verified decision {feedback_id}: correct={correct}")
                return record
        
        logger.warning(f"Feedback record not found: {feedback_id}")
        return None
    
    def get_pending_verifications(self) -> list[FeedbackRecord]:
        """Get decisions that need verification."""
        records = self._load_records(self.decisions_file)
        pending = []
        
        for record_data in records:
            record = FeedbackRecord.model_validate(record_data)
            if record.verified_at is None:
                pending.append(record)
        
        return pending
    
    def get_overdue_verifications(self) -> list[FeedbackRecord]:
        """Get decisions past their reminder time."""
        records = self.get_pending_verifications()
        now = datetime.now()
        
        return [
            r for r in records
            if r.reminder_at and r.reminder_at <= now
        ]
    
    def get_accuracy_stats(
        self,
        decision_type: str | None = None,
        days: int | None = None,
    ) -> dict[str, Any]:
        """
        Get accuracy statistics.
        
        Args:
            decision_type: Filter by decision type (e.g., "buy", "sell")
            days: Only include records from last N days
            
        Returns:
            Statistics dictionary
        """
        records = self._load_records(self.decisions_file)
        verified = []
        
        cutoff = datetime.now() - timedelta(days=days) if days else None
        
        for record_data in records:
            record = FeedbackRecord.model_validate(record_data)
            
            if record.verified_at is None:
                continue
            
            if decision_type and record.decision != decision_type:
                continue
            
            if cutoff and record.created_at < cutoff:
                continue
            
            verified.append(record)
        
        if not verified:
            return {
                "total": 0,
                "verified": 0,
                "correct": 0,
                "accuracy": None,
            }
        
        correct = sum(1 for r in verified if r.outcome_correct)
        
        return {
            "total": len(records),
            "verified": len(verified),
            "correct": correct,
            "accuracy": correct / len(verified) if verified else None,
            "by_decision_type": self._group_by_decision(verified),
        }
    
    def _group_by_decision(self, records: list[FeedbackRecord]) -> dict[str, dict]:
        """Group accuracy by decision type."""
        grouped: dict[str, list[FeedbackRecord]] = {}
        
        for record in records:
            decision = record.decision
            if decision not in grouped:
                grouped[decision] = []
            grouped[decision].append(record)
        
        result = {}
        for decision, recs in grouped.items():
            correct = sum(1 for r in recs if r.outcome_correct)
            result[decision] = {
                "count": len(recs),
                "correct": correct,
                "accuracy": correct / len(recs) if recs else None,
            }
        
        return result
    
    def _load_records(self, file_path: Path) -> list[dict]:
        """Load records from JSONL file."""
        if not file_path.exists():
            return []
        
        records = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return records
    
    def _append_record(self, file_path: Path, record: dict):
        """Append a record to JSONL file."""
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    
    def _update_record(
        self,
        file_path: Path,
        key_field: str,
        key_value: str,
        updated: dict,
    ):
        """Update a record in JSONL file."""
        records = self._load_records(file_path)
        
        for i, record in enumerate(records):
            if record.get(key_field) == key_value:
                records[i] = updated
                break
        
        with open(file_path, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

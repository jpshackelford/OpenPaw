"""Queue manager for multi-conversation orchestration.

This module provides the QueueManager class that:
- Adds conversations to a persistent queue
- Processes queued items at configurable intervals (heartbeat)
- Integrates with ConversationRunner for execution
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from openpaws.config import QueueConfig
from openpaws.runner import ConversationRunner
from openpaws.storage import QueueItem, Storage

logger = logging.getLogger(__name__)


@dataclass
class QueueManager:
    """Manages the conversation queue for multi-conversation workflows.

    The QueueManager coordinates between:
    - Storage: Persists queue items to SQLite
    - ConversationRunner: Executes queued conversations
    - Config: Controls heartbeat interval and max dispatch

    Example:
        >>> from openpaws.config import QueueConfig
        >>> from openpaws.storage import Storage
        >>> from openpaws.runner import ConversationRunner
        >>>
        >>> qm = QueueManager(
        ...     storage=Storage(),
        ...     runner=runner,
        ...     config=QueueConfig(heartbeat_interval=60, max_dispatch=3),
        ... )
        >>> item_id = await qm.enqueue("Analyze results", "main", context={"step": 1})
        >>> processed = await qm.process_batch()
    """

    storage: Storage
    runner: ConversationRunner
    config: QueueConfig = field(default_factory=QueueConfig)

    async def enqueue(
        self,
        prompt: str,
        group_name: str,
        *,
        context: dict | None = None,
        priority: int = 0,
        parent_conversation_id: str | None = None,
        workflow_id: str | None = None,
    ) -> str:
        """Add a conversation to the queue.

        Args:
            prompt: The prompt for the queued conversation.
            group_name: The group to run the conversation in.
            context: Optional context dict to pass to the conversation.
            priority: Priority level (higher = processed first). Default 0.
            parent_conversation_id: ID of the parent conversation (for tracing).
            workflow_id: ID to group related queue items.

        Returns:
            The ID of the queued item.
        """
        item = QueueItem.create(
            prompt=prompt,
            group_name=group_name,
            context=context,
            priority=priority,
            parent_conversation_id=parent_conversation_id,
            workflow_id=workflow_id,
        )
        self.storage.enqueue(item)
        logger.info(
            f"Queued conversation for group '{group_name}' with id={item.id[:8]}... "
            f"priority={priority}"
        )
        return item.id

    def _build_prompt_with_context(self, item: QueueItem) -> str:
        """Build the prompt with context prepended if available."""
        if not item.context:
            return item.prompt
        context_str = "\n".join(f"- {k}: {v}" for k, v in item.context.items())
        return f"Context from previous conversation:\n{context_str}\n\n{item.prompt}"

    async def _process_item(self, item: QueueItem) -> None:
        """Process a single queue item."""
        logger.info(
            f"Processing queue item {item.id[:8]}... for group '{item.group_name}'"
        )
        try:
            prompt = self._build_prompt_with_context(item)
            result = await self.runner.run_prompt(
                group_name=item.group_name,
                prompt=prompt,
            )
            if result.success:
                self.storage.complete_queue_item(item.id, result.message)
                logger.info(f"Queue item {item.id[:8]}... completed successfully")
            else:
                self.storage.fail_queue_item(item.id, result.error or "Unknown error")
                logger.warning(f"Queue item {item.id[:8]}... failed: {result.error}")
        except Exception as e:
            self.storage.fail_queue_item(item.id, str(e))
            logger.exception(f"Queue item {item.id[:8]}... failed with exception")

    async def process_batch(self) -> int:
        """Process up to max_dispatch items from the queue.

        Items are processed in priority order (highest first), then FIFO.
        Each item is marked as 'processing' before execution to prevent
        duplicate processing.

        Returns:
            The number of items processed.
        """
        if not self.config.enabled:
            return 0

        items = self.storage.dequeue(max_items=self.config.max_dispatch)
        if not items:
            return 0

        for item in items:
            await self._process_item(item)

        return len(items)

    def get_stats(self) -> dict[str, int]:
        """Get queue statistics by status."""
        return self.storage.get_queue_stats()

    def list_pending(self) -> list[QueueItem]:
        """List all pending queue items."""
        return self.storage.list_queue(status="pending")

    def clear_completed(self) -> int:
        """Clear all completed queue items. Returns count deleted."""
        return self.storage.clear_queue(status="completed")

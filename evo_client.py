"""Evolink API client for image generation and task polling."""
import asyncio
import time
from typing import Any, Optional

import aiohttp

from config import Settings


class EvoClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

    async def create_task(self, prompt: str, image_urls: list[str]) -> str:
        payload: dict[str, Any] = {
            "model": self.settings.image_model,
            "prompt": prompt,
            "size": self.settings.image_size,
            "quality": self.settings.image_quality,
        }
        if image_urls:
            payload["image_urls"] = image_urls

        url = f"{self.settings.api_base_url}/v1/images/generations"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=self.headers, json=payload, timeout=90) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"Create task failed [{resp.status}]: {text}")
                data = await resp.json()

        task_id = data.get("id")
        if not task_id:
            raise RuntimeError(f"Task id not found in response: {data}")
        return task_id

    async def get_task(self, task_id: str) -> dict[str, Any]:
        url = f"{self.settings.api_base_url}/v1/tasks/{task_id}"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=45,
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"Get task failed [{resp.status}]: {text}")
                return await resp.json()

    async def wait_for_completion(
        self,
        task_id: str,
        on_progress: Optional[Any] = None,
    ) -> dict[str, Any]:
        started = time.time()
        while True:
            details = await self.get_task(task_id)
            status = details.get("status")
            progress = details.get("progress")
            if on_progress is not None:
                await on_progress(status, progress)
            if status in {"completed", "failed"}:
                return details
            if time.time() - started > self.settings.task_timeout_seconds:
                raise TimeoutError("Task polling timeout")
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def get_credits(self) -> dict[str, Any]:
        url = f"{self.settings.api_base_url}/v1/credits"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                timeout=30,
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise RuntimeError(f"Get credits failed [{resp.status}]: {text}")
                return await resp.json()

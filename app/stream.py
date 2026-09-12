from typing import Any


class RedisStreamBus:
    """Best-effort Redis Streams transport with a local-runtime fallback."""

    def __init__(self, url: str, stream: str = "harness:runs") -> None:
        self.url = url
        self.stream = stream
        self.client: Any = None
        try:
            from redis.asyncio import Redis
            self.client = Redis.from_url(url, decode_responses=True, socket_connect_timeout=0.2, socket_timeout=0.5)
        except Exception:
            self.client = None

    async def publish(self, run_id: str) -> bool:
        if not self.client:
            return False
        try:
            await self.client.xadd(self.stream, {"run_id": run_id}, maxlen=10000, approximate=True)
            return True
        except Exception:
            return False

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()

    async def consume_forever(self, callback) -> None:
        if not self.client:
            while True:
                await __import__("asyncio").sleep(5)
        group = "harness-workers"
        consumer = f"worker-{id(self)}"
        try:
            await self.client.xgroup_create(self.stream, group, id="0", mkstream=True)
        except Exception:
            pass
        while True:
            try:
                pending = await self.client.xautoclaim(self.stream, group, consumer, min_idle_time=1000, start_id="0-0", count=10)
                for message_id, values in pending[1]:
                    await self._handle_message(group, message_id, values, callback)
                batches = await self.client.xreadgroup(group, consumer, {self.stream: ">"}, count=1, block=5000)
                for _stream, messages in batches:
                    for message_id, values in messages:
                        await self._handle_message(group, message_id, values, callback)
            except Exception:
                await __import__("asyncio").sleep(2)

    async def _handle_message(self, group: str, message_id: str, values: dict[str, Any], callback) -> None:
        run_id = values.get("run_id")
        if run_id:
            await callback(run_id)
        await self.client.xack(self.stream, group, message_id)

import asyncio

from .db import init_db
from .runtime import runtime


async def main() -> None:
    init_db()
    await runtime.stream.consume_forever(runtime.execute)


if __name__ == "__main__":
    asyncio.run(main())

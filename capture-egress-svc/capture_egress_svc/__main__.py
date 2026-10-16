import asyncio

from .proxy import serve

if __name__ == "__main__":
    asyncio.run(serve())

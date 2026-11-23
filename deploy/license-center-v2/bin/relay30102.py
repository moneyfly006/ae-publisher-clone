import asyncio

TARGET_HOST = "8.136.27.44"
TARGET_PORT = 30102
LISTEN_PORT = 30102

async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass

async def handle(reader, writer):
    try:
        tr, tw = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
    except Exception:
        writer.close()
        return
    await asyncio.gather(pipe(reader, tw), pipe(tr, writer))

async def main():
    srv = await asyncio.start_server(handle, "0.0.0.0", LISTEN_PORT)
    async with srv:
        await srv.serve_forever()

asyncio.run(main())

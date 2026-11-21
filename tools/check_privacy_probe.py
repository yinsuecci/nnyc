"""Windows-only small-window compositor check; no full screen, remote input or audio changes."""
import multiprocessing as mp
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from privacy_curtain import _worker


def main():
    if sys.platform != "win32":
        raise SystemExit("Windows required")
    ctx = mp.get_context("spawn")
    reader, writer = ctx.Pipe()
    process = ctx.Process(target=_worker, args=(writer, "本机兼容性测试", "lifecycle"))
    process.start()
    writer.close()
    try:
        if not reader.poll(12):
            raise SystemExit("Probe timed out")
        status = reader.recv()
        if status != "covered":
            raise SystemExit(status)
        reader.send("check")
        if not reader.poll(2) or reader.recv() != "covered":
            raise SystemExit("Live cover verification failed")
    finally:
        reader.close()
        process.join(2)
        if process.is_alive():
            process.terminate()
            process.join()
            raise SystemExit("Failed to release curtain on channel close")
    if process.exitcode != 0:
        raise SystemExit("Unexpected worker exit")
    print("PASS: small physical curtain sample, independent capture, health and parent-pipe cleanup")


if __name__ == "__main__":
    main()

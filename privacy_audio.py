"""Session-scoped playback mute. The guardian owns restoration, not the UI."""
import multiprocessing as mp


def playback_endpoints():
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    from pycaw.constants import EDataFlow, DEVICE_STATE
    enumerator = AudioUtilities.GetDeviceEnumerator()
    devices = enumerator.EnumAudioEndpoints(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
    result = {}
    for i in range(devices.GetCount()):
        endpoint = devices.Item(i)
        result[endpoint.GetId()] = endpoint.Activate(
            IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
    return result


class MuteLease:
    def __init__(self, enumerate_outputs=playback_endpoints):
        self.outputs = enumerate_outputs
        self.original = {}

    def enforce(self):
        endpoints = self.outputs()
        if not endpoints:
            raise RuntimeError("没有可验证的播放端点")
        if self.original.keys() - endpoints.keys():
            raise RuntimeError("播放设备已断开")
        for key, endpoint in endpoints.items():
            muted = bool(endpoint.GetMute())
            if key not in self.original:
                self.original[key] = (endpoint, muted)
            if not muted:
                endpoint.SetMute(1, None)
            if not endpoint.GetMute():
                raise RuntimeError("播放设备未确认静音")

    def restore(self):
        failed = []
        for key, (endpoint, muted) in list(self.original.items()):
            try:
                if bool(endpoint.GetMute()) != muted:
                    endpoint.SetMute(int(muted), None)
                if bool(endpoint.GetMute()) != muted:
                    raise RuntimeError()
                del self.original[key]
            except Exception:
                failed.append(key)
        if failed:
            raise RuntimeError("无法验证所有播放设备均已恢复")


def guard(pipe, factory=MuteLease):
    lease = factory()
    try:
        lease.enforce()
        pipe.send("muted")
        while not pipe.poll(.25):
            lease.enforce()
        # Readable also means the parent exited. Either way, release the lease.
    except (BrokenPipeError, EOFError):
        pass
    except Exception:
        try:
            pipe.send("failed")
        except OSError:
            pass
    finally:
        try:
            lease.restore()
            status = "restored"
        except Exception:
            status = "restore_failed"
        try:
            pipe.send(status)
        except OSError:
            pass
        pipe.close()


class AudioGuard:
    def __init__(self):
        ctx = mp.get_context("spawn")
        self.pipe, child = ctx.Pipe()
        self.process = ctx.Process(target=guard, args=(child,))
        try:
            self.process.start()
        except BaseException:
            self.pipe.close()
            raise
        finally:
            child.close()

    def read(self):
        return self.pipe.recv() if self.pipe.poll() else None

    def stop(self):
        try:
            self.pipe.send("stop")
        except (BrokenPipeError, EOFError, OSError):
            pass

    def close(self):
        self.pipe.close()
        # Do not terminate a child in the middle of restoring audio.
        self.process.join(timeout=2)
        if not self.process.is_alive():
            self.process.close()

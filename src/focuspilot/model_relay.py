"""Encrypted TLS byte relay to one fixed provider. Agent has zero network caps.

The host never accepts a hostname/port from the peer and never decrypts API keys
or model content. The Node client performs TLS certificate/hostname verification.
"""

import socket
import time

from .mcp_bridge import PipeRelay, BridgeRejected


class ModelRelay(PipeRelay):
    def _run(self):
        import pywintypes
        import win32file
        import win32pipe
        self.report.update(connections=0, bytes_to_model=0, bytes_from_model=0)
        self.remote = None
        try:
            while not self.stop.is_set():
                self.policy.check(time.monotonic())
                try:
                    win32pipe.ConnectNamedPipe(self.pipe, None)
                except pywintypes.error as error:
                    if error.winerror == 536:
                        time.sleep(.01)
                        continue
                    if error.winerror != 535:
                        raise
                self.verify_peer()
                self.report['connections'] += 1
                if self.report['connections'] > 4:
                    raise BridgeRejected('model_connection_budget_exceeded')
                # Literal target only: peer sends TLS bytes, never a CONNECT URL.
                self.remote = socket.create_connection(('api.deepseek.com', 443), timeout=5)
                self.remote.setblocking(False)
                upstream, downstream = bytearray(), bytearray()
                remote_ended = False
                while not self.stop.is_set():
                    self.policy.check(time.monotonic())
                    try:
                        _, available, _ = win32pipe.PeekNamedPipe(self.pipe, 0)
                        if available:
                            _, data = win32file.ReadFile(self.pipe, min(available, 16384))
                            upstream.extend(data)
                            self.report['bytes_to_model'] += len(data)
                    except pywintypes.error as error:
                        if error.winerror in (109, 232, 233):
                            break
                        raise
                    try:
                        if upstream:
                            count = self.remote.send(upstream)
                            del upstream[:count]
                    except BlockingIOError:
                        pass
                    try:
                        if not remote_ended and len(downstream) < 131072:
                            data = self.remote.recv(16384)
                            if not data:
                                remote_ended = True
                            downstream.extend(data)
                            self.report['bytes_from_model'] += len(data)
                    except BlockingIOError:
                        pass
                    if len(upstream)>262144 or self.report['bytes_to_model']+self.report['bytes_from_model']>4194304:
                        raise BridgeRejected('model_byte_budget_exceeded')
                    if downstream:
                        _, written = win32file.WriteFile(self.pipe, bytes(downstream[:16384]))
                        del downstream[:written]
                    if remote_ended and not downstream:
                        break
                    time.sleep(.005)
                self.remote.close()
                self.remote = None
                win32pipe.DisconnectNamedPipe(self.pipe)
        except Exception as error:
            if not self.stop.is_set():
                self.error = str(error) if isinstance(error, BridgeRejected) else 'model_relay_failed'
        finally:
            self.policy.cancel()
            if self.remote:
                self.remote.close()
            try:
                win32pipe.DisconnectNamedPipe(self.pipe)
            except pywintypes.error:
                pass
            self.report['model_socket_closed'] = True

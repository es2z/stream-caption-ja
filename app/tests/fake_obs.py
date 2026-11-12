"""テスト用の偽 OBS (obs-websocket v5 の最小限の振る舞い)。"""

import json

import websockets

from stream_caption.sinks.obs import auth_string


class FakeObs:
    def __init__(self, password: str = "secret"):
        self.password = password
        self.streaming = True
        self.captions: list[str] = []
        self.server = None
        self.port = None

    async def __aenter__(self):
        self.server = await websockets.serve(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, ws):
        salt, challenge = "salt123", "challenge456"
        hello = {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}
        if self.password:
            hello["authentication"] = {"salt": salt, "challenge": challenge}
        await ws.send(json.dumps({"op": 0, "d": hello}))
        identify = json.loads(await ws.recv())
        if self.password and identify["d"].get("authentication") != auth_string(
            self.password, salt, challenge
        ):
            await ws.close(4009, "Authentication failed")
            return
        await ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        async for raw in ws:
            msg = json.loads(raw)
            d = msg["d"]
            if d["requestType"] == "SendStreamCaption":
                if self.streaming:
                    self.captions.append(d["requestData"]["captionText"])
                    status = {"result": True, "code": 100}
                else:
                    status = {"result": False, "code": 501, "comment": "output not running"}
            else:
                status = {"result": False, "code": 204}
            await ws.send(
                json.dumps(
                    {
                        "op": 7,
                        "d": {
                            "requestType": d["requestType"],
                            "requestId": d["requestId"],
                            "requestStatus": status,
                        },
                    }
                )
            )

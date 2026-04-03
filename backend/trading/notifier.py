from __future__ import annotations

import json
import urllib.request
from datetime import datetime

from backend.config import settings


class FeishuNotifier:
    def __init__(self) -> None:
        self.enabled = settings.feishu_alert_enabled and bool(settings.feishu_webhook_url.strip())
        self.webhook_url = settings.feishu_webhook_url.strip()

    def send(self, title: str, text: str) -> None:
        if not self.enabled:
            return
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": [
                            [
                                {
                                    "tag": "text",
                                    "text": f"[{datetime.utcnow().isoformat()} UTC] {text}",
                                }
                            ]
                        ],
                    }
                }
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass

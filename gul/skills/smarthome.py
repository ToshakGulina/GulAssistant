"""Умный дом по MQTT и (опционально) выключение неттопа голосом."""

from __future__ import annotations

import logging
import re
import subprocess

from .base import Skill, SkillContext, SkillResult

log = logging.getLogger(__name__)


class SmartHomeSkill(Skill):
    name = "smarthome"
    title = "Умный дом (MQTT)"
    description = "Включает и выключает устройства, настроенные в config.yaml."
    examples = ["включи свет на кухне", "выключи розетку"]
    priority = 60
    visible = True

    def __init__(self):
        self._client = None
        self._connected = False
        self._lock = None

    def _connect(self, ctx: SkillContext):
        if self._client is not None:
            return self._client
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            log.warning("paho-mqtt не установлен — умный дом недоступен")
            return None
        cfg = ctx.cfg.section("skills").get("mqtt", {}) or {}
        if not cfg.get("enabled"):
            return None
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            if cfg.get("username"):
                client.username_pw_set(cfg["username"], cfg.get("password", ""))
            client.connect(cfg.get("host", "127.0.0.1"), int(cfg.get("port", 1883)), keepalive=30)
            client.loop_start()
            self._client = client
            self._connected = True
            return client
        except Exception as exc:
            log.warning("MQTT не подключился: %s", exc)
            self._client = None
            return None

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        devices = ctx.get("skills.mqtt.devices", []) or []
        if not devices:
            return None
        if not re.search(r"(включи|выключи|зажги|погаси|открой|закрой)", text):
            return None

        target = None
        for device in devices:
            name = device.get("name", "").lower().replace("ё", "е")
            if name and name in text:
                target = device
                break
        if target is None:
            # Попробуем по отдельным словам: «свет на кухне» → device "свет на кухне".
            for device in devices:
                words = device.get("name", "").lower().split()
                if words and all(w in text for w in words):
                    target = device
                    break
        if target is None:
            return None

        turn_on = bool(re.search(r"(включи|зажги|открой|подними)", text))
        payload = target.get("on", "ON") if turn_on else target.get("off", "OFF")
        client = self._connect(ctx)
        if client is None:
            return SkillResult(
                text="Умный дом не настроен. Включите MQTT в config.yaml и укажите брокер."
            )
        try:
            client.publish(target["topic"], payload)
            return SkillResult(text=f"{'Включаю' if turn_on else 'Выключаю'} {target['name']}.")
        except Exception as exc:
            log.warning("MQTT publish: %s", exc)
            return SkillResult(text=f"Не удалось отправить команду: {exc}")


class SystemSkill(Skill):
    name = "system"
    title = "Управление неттопом"
    description = "Выключение и перезагрузка голосом (по умолчанию отключено)."
    examples = ["выключи компьютер", "перезагрузи компьютер"]
    priority = 40

    def handle(self, text: str, ctx: SkillContext) -> SkillResult | None:
        if not re.search(r"(выключи|отключи|перезагрузи|перезагрузка).{0,15}(компьютер|неттоп|систему|машину)", text):
            return None
        if not ctx.get("skills.system.allow_shutdown", False):
            return SkillResult(
                text="Выключение голосом отключено в настройках. Если хотите — включите "
                     "skills.system.allow_shutdown в config.yaml."
            )
        action = "poweroff" if "перезагруз" not in text else "reboot"
        try:
            subprocess.Popen(["systemctl", action])
            return SkillResult(text="Выключаю компьютер. До связи!" if action == "poweroff"
                               else "Перезагружаю компьютер.")
        except Exception as exc:
            return SkillResult(text=f"Не удалось выполнить: {exc}")

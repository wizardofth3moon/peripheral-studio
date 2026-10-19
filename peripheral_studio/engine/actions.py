"""Runs actions: what happens on press and on release of a bound button."""
from __future__ import annotations

import asyncio
import logging

from evdev import ecodes as e

from . import model
from .output import Output

log = logging.getLogger("engine.actions")


def _codes(names: list[str]) -> list[int]:
    out = []
    for n in names:
        try:
            out.append(model.code_of(n))
        except ValueError:
            log.warning("skipping unknown key %s", n)
    return out


class Running:
    """One press of a bound button. `release()` is called exactly once, on button release."""

    def release(self) -> None:
        pass


class HeldKeys(Running):
    def __init__(self, out: Output, codes: list[int]):
        self.out, self.codes = out, codes
        for c in codes:
            out.down(c)

    def release(self):
        for c in reversed(self.codes):
            self.out.up(c)


class Task(Running):
    """An asyncio task (turbo, macros) cancelled on release unless it should outlive it."""

    def __init__(self, coro, stop_on_release: bool, out: Output, cleanup=None):
        self.out, self.stop_on_release, self.cleanup = out, stop_on_release, cleanup
        self.task = asyncio.get_running_loop().create_task(coro)
        self.task.add_done_callback(self._done)

    def _done(self, _task):
        if self.cleanup:
            self.cleanup()

    def release(self):
        if self.stop_on_release:
            self.task.cancel()

    def stop(self):
        self.task.cancel()


async def _turbo(out: Output, codes: list[int], interval: float):
    try:
        while True:
            for c in codes:
                out.down(c)
            await asyncio.sleep(min(0.015, interval / 2))
            for c in reversed(codes):
                out.up(c)
            await asyncio.sleep(max(0.005, interval - min(0.015, interval / 2)))
    finally:
        for c in codes:
            if out.held.get(c):
                out.up(c)


async def _macro_once(out: Output, macro: model.MacroAction, pressed: set[int]):
    for step in macro.steps:
        if step.op == "wait":
            await asyncio.sleep(step.ms / 1000)
        elif step.op == "text":
            await out.type_text(step.text)
        elif step.op == "scroll":
            out.scroll(step.axis, step.amount)
        else:
            try:
                code = model.code_of(step.key)
            except ValueError:
                continue
            if step.op == "down":
                out.down(code)
                pressed.add(code)
            elif step.op == "up":
                if code in pressed:
                    out.up(code)
                    pressed.discard(code)
            else:
                out.tap(code)
                await asyncio.sleep(0.002)


async def _macro(out: Output, macro: model.MacroAction):
    pressed: set[int] = set()
    try:
        if macro.mode in ("while_held", "toggle"):
            while True:
                await _macro_once(out, macro, pressed)
                await asyncio.sleep(0.001)  # an empty or all-tap macro must still yield
        else:
            for _ in range(macro.count if macro.mode == "repeat" else 1):
                await _macro_once(out, macro, pressed)
    finally:
        for code in pressed:  # whatever the macro pressed and never released
            out.up(code)


class Runner:
    """Starts actions for an engine. Toggle macros live here between presses."""

    def __init__(self, out: Output):
        self.out = out
        self.toggles: dict[tuple, Task] = {}

    def press(self, slot: tuple, action: model.Action, engine) -> Running:
        out = self.out
        if isinstance(action, model.KeysAction):
            return HeldKeys(out, _codes(action.keys))
        if isinstance(action, model.ScrollAction):
            out.scroll(action.axis, action.amount)
            return Running()
        if isinstance(action, model.TurboAction):
            return Task(_turbo(out, _codes(action.keys), action.interval_ms / 1000), True, out)
        if isinstance(action, model.MacroAction):
            if action.mode == "toggle":
                if running := self.toggles.pop(slot, None):
                    running.stop()
                    return Running()
                t = Task(_macro(out, action), False, out, cleanup=lambda: self.toggles.pop(slot, None))
                self.toggles[slot] = t
                return Running()
            return Task(_macro(out, action), action.mode == "while_held", out)
        if isinstance(action, model.HypershiftAction):
            return engine.hold_hypershift()
        if isinstance(action, model.DpiAction):
            engine.step_dpi(action.step)
            return Running()
        if isinstance(action, model.ProfileAction):
            engine.switch_profile_from(action.device or slot[0], action.target, action.group)
            return Running()
        return Running()  # disable / unknown: swallow the press

    def stop_all(self):
        for t in list(self.toggles.values()):
            t.stop()
        self.toggles.clear()
        self.out.release_all()

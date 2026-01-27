from __future__ import annotations

import curses
from dataclasses import dataclass


@dataclass
class InputState:
    symbols: list[str]
    index: int = 0
    search_mode: bool = False
    search_buffer: str = ""

    @property
    def symbol(self) -> str:
        return self.symbols[self.index]

    def handle_key(self, key: int) -> None:
        if self.search_mode:
            self._handle_search_key(key)
            return

        if key in (curses.KEY_LEFT, ord("h")):
            self.index = (self.index - 1) % len(self.symbols)
        elif key in (curses.KEY_RIGHT, ord("l")):
            self.index = (self.index + 1) % len(self.symbols)
        elif key == ord("/"):
            self.search_mode = True
            self.search_buffer = ""

    def _handle_search_key(self, key: int) -> None:
        if key in (27,):  # ESC
            self.search_mode = False
            self.search_buffer = ""
            return

        if key in (curses.KEY_ENTER, 10, 13):
            match = _find_symbol(self.symbols, self.search_buffer)
            if match is not None:
                self.index = match
            self.search_mode = False
            self.search_buffer = ""
            return

        if key in (curses.KEY_BACKSPACE, 127, 8):
            self.search_buffer = self.search_buffer[:-1]
            return

        if 32 <= key <= 126:
            self.search_buffer += chr(key).upper()


def _find_symbol(symbols: list[str], query: str) -> int | None:
    if not query:
        return None
    query_upper = query.upper()
    for idx, symbol in enumerate(symbols):
        if symbol.upper().startswith(query_upper):
            return idx
    return None

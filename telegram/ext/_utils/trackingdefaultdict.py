#!/usr/bin/env python
#
# A library that provides a Python interface to the Telegram Bot API
# Copyright (C) 2015-2022
# Leandro Toledo de Souza <devs@python-telegram-bot.org>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser Public License for more details.
#
# You should have received a copy of the GNU Lesser Public License
# along with this program.  If not, see [http://www.gnu.org/licenses/].
"""This module contains a subclass of :class:`collections.defaultdict` that keeps track of the
keys thet where accessed.

.. versionadded:: 14.0

Warning:
    Contents of this module are intended to be used internally by the library and *not* by the
    user. Changes to this module are not considered breaking changes and may not be documented in
    the changelog.
"""
from typing import TypeVar, DefaultDict, Callable, Set

_DDT = TypeVar('_DDT')


class _TrackingDefaultDict(DefaultDict[int, _DDT]):
    """DefaultDict that keeps track of which keys where accessed."""

    def __init__(self, default_factory: Callable[[], _DDT]):
        # The default_factory argument for defaultdict is positional only!
        super().__init__(default_factory)
        self._accessed_keys: Set[int] = set()

    def __getitem__(self, key: int) -> _DDT:
        item = super().__getitem__(key)
        self._accessed_keys.add(key)
        return item

    # super().get has no type hint for default, so mypy is complaining …
    def get(self, key: int, default: _DDT = None) -> _DDT:  # type: ignore[override]
        # For some reason `get` doesn't call __getitem__, so we have to override this as well …
        if key in self:
            self._accessed_keys.add(key)
        item = super().get(
            key,
            # for some reason mypy and pylint have issues with self.default_factory
            default or self.default_factory(),  # type: ignore[misc] # pylint: disable=not-callable
        )
        return item

    def pop_accessed_keys(self) -> Set[int]:
        """Returns a set of keys accessed since the last time this method was called."""
        accessed_keys = self._accessed_keys
        self._accessed_keys = set()
        return accessed_keys

    def get_without_tracking(self, key: int) -> _DDT:
        """Grants access to a key without tracking the access."""
        return super().__getitem__(key)

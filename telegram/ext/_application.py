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
"""This module contains the Application class."""
import asyncio
import inspect
import logging
from asyncio import Event
from collections import defaultdict
from pathlib import Path
from types import TracebackType
from typing import (
    Callable,
    DefaultDict,
    Dict,
    List,
    Optional,
    Union,
    Generic,
    TypeVar,
    TYPE_CHECKING,
    Type,
    Tuple,
    Coroutine,
    Any,
    Set,
)

from telegram import Update
from telegram._utils.types import DVInput, ODVInput
from telegram.error import TelegramError
from telegram.ext import BasePersistence, ContextTypes, ExtBot, Updater
from telegram.ext._handler import Handler
from telegram.ext._callbackdatacache import CallbackDataCache
from telegram._utils.defaultvalue import DefaultValue, DEFAULT_TRUE, DEFAULT_NONE
from telegram._utils.warnings import warn
from telegram.ext._utils.types import CCT, UD, CD, BD, BT, JQ, HandlerCallback
from telegram.ext._utils.stack import was_called_by

if TYPE_CHECKING:
    from telegram.ext._jobqueue import Job
    from telegram.ext._builders import InitApplicationBuilder

DEFAULT_GROUP: int = 0

_UT = TypeVar('_UT')
_DispType = TypeVar('_DispType', bound="Application")
_RT = TypeVar('_RT')
_STOP_SIGNAL = object()

_logger = logging.getLogger(__name__)


class ApplicationHandlerStop(Exception):
    """
    Raise this in a handler or an error handler to prevent execution of any other handler (even in
    different group).

    In order to use this exception in a :class:`telegram.ext.ConversationHandler`, pass the
    optional ``state`` parameter instead of returning the next state:

    .. code-block:: python

        def callback(update, context):
            ...
            raise ApplicationHandlerStop(next_state)

    Note:
        Has no effect, if the handler or error handler is run asynchronously.

    Attributes:
        state (:obj:`object`): Optional. The next state of the conversation.

    Args:
        state (:obj:`object`, optional): The next state of the conversation.
    """

    __slots__ = ('state',)

    def __init__(self, state: object = None) -> None:
        super().__init__()
        self.state = state


class Application(Generic[BT, CCT, UD, CD, BD, JQ]):
    """This class dispatches all kinds of updates to its registered handlers.

    Note:
         This class may not be initialized directly. Use :class:`telegram.ext.ApplicationBuilder`
         or :meth:`builder` (for convenience).

    .. versionchanged:: 14.0

        * Initialization is now done through the :class:`telegram.ext.ApplicationBuilder`.
        * Removed the attribute ``groups``.

    Attributes:
        bot (:class:`telegram.Bot`): The bot object that should be passed to the handlers.
        update_queue (:class:`asyncio.Queue`): The synchronized queue that will contain the
            updates.
        job_queue (:class:`telegram.ext.JobQueue`): Optional. The :class:`telegram.ext.JobQueue`
            instance to pass onto handler callbacks.
        concurrent_updates (:obj:`int`, optional): Number of maximum concurrent worker threads for
            the ``@run_async`` decorator and :meth:`run_async`.
        user_data (:obj:`defaultdict`): A dictionary handlers can use to store data for the user.
        chat_data (:obj:`defaultdict`): A dictionary handlers can use to store data for the chat.
        bot_data (:obj:`dict`): A dictionary handlers can use to store data for the bot.
        persistence (:class:`telegram.ext.BasePersistence`): Optional. The persistence class to
            store data that should be persistent over restarts.
        handlers (Dict[:obj:`int`, List[:class:`telegram.ext.Handler`]]): A dictionary mapping each
            handler group to the list of handlers registered to that group.

            .. seealso::
                :meth:`add_handler`, :meth:`add_handlers`.
        error_handlers (Dict[:obj:`callable`, :obj:`bool`]): A dict, where the keys are error
            handlers and the values indicate whether they are to be run asynchronously via
            :meth:`run_async`.

            .. seealso::
                :meth:`add_error_handler`

    """

    # Allowing '__weakref__' creation here since we need it for the JobQueue
    __slots__ = (
        '__weakref__',
        '_concurrent_updates',
        'updater',
        'persistence',
        'update_queue',
        'job_queue',
        'user_data',
        'chat_data',
        'bot_data',
        '_update_persistence_lock',
        '_concurrent_updates_sem',
        'handlers',
        'error_handlers',
        '_running',
        '__create_task_tasks',
        '__update_fetcher_task',
        'bot',
        'context_types',
    )

    def __init__(
        self: 'Application[BT, CCT, UD, CD, BD, JQ]',
        *,
        bot: BT,
        update_queue: asyncio.Queue,
        updater: Optional[Updater],
        job_queue: JQ,
        concurrent_updates: Union[bool, int],
        persistence: Optional[BasePersistence],
        context_types: ContextTypes[CCT, UD, CD, BD],
    ):
        if not was_called_by(
            inspect.currentframe(), Path(__file__).parent.resolve() / '_builders.py'
        ):
            warn(
                '`Application` instances should be built via the `ApplicationBuilder`.',
                stacklevel=2,
            )

        self.bot = bot
        self.update_queue = update_queue
        self.job_queue = job_queue
        self.context_types = context_types
        self.updater = updater

        if isinstance(concurrent_updates, int) and concurrent_updates < 0:
            raise ValueError('`concurrent_updates` must be a non-negative integer!')

        if concurrent_updates is True:
            concurrent_updates = 4096
        self._concurrent_updates_sem = asyncio.BoundedSemaphore(concurrent_updates or 1)
        self._concurrent_updates = bool(concurrent_updates)

        if self.job_queue:
            self.job_queue.set_application(self)

        self.user_data: DefaultDict[int, UD] = defaultdict(self.context_types.user_data)
        self.chat_data: DefaultDict[int, CD] = defaultdict(self.context_types.chat_data)
        self.bot_data = self.context_types.bot_data()
        self.persistence: Optional[BasePersistence] = None
        self._update_persistence_lock = asyncio.Lock()

        if not isinstance(persistence, BasePersistence):
            raise TypeError("persistence must be based on telegram.ext.BasePersistence")

        self.persistence = persistence

        self.handlers: Dict[int, List[Handler]] = {}
        self.error_handlers: Dict[Callable, Union[bool, DefaultValue]] = {}

        # A number of low-level helpers for the internal logic
        self._running = False
        self.__update_fetcher_task: Optional[asyncio.Task] = None
        self.__create_task_tasks: Set[asyncio.Task] = set()

    @property
    def running(self) -> bool:
        """:obj:`bool`: Indicates if this application is running.

        .. seealso::
            :meth:`start`, :meth:`stop`
        """
        return self._running

    @property
    def concurrent_updates(self) -> bool:
        return self._concurrent_updates

    async def initialize(self) -> None:
        await self.bot.initialize()
        if self.updater:
            await self.updater.initialize()
        await self._initialize_persistence()

    async def shutdown(self) -> None:
        await self.bot.shutdown()
        if self.updater:
            await self.updater.shutdown()

    async def __aenter__(self: _DispType) -> _DispType:
        try:
            await self.initialize()
            return self
        except Exception as exc:
            await self.shutdown()
            raise exc

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        # Make sure not to return `True` so that exceptions are not suppressed
        # https://docs.python.org/3/reference/datamodel.html?#object.__aexit__
        await self.shutdown()

    async def _initialize_persistence(self) -> None:
        if not self.persistence:
            return

        # This raises an exception if persistence.store_data.callback_data is True
        # but self.bot is not an instance of ExtBot - so no need to check that later on
        self.persistence.set_bot(self.bot)

        if self.persistence.store_data.user_data:
            self.user_data = await self.persistence.get_user_data()
            if not isinstance(self.user_data, defaultdict):
                raise ValueError("user_data must be of type defaultdict")
        if self.persistence.store_data.chat_data:
            self.chat_data = await self.persistence.get_chat_data()
            if not isinstance(self.chat_data, defaultdict):
                raise ValueError("chat_data must be of type defaultdict")
        if self.persistence.store_data.bot_data:
            self.bot_data = await self.persistence.get_bot_data()
            if not isinstance(self.bot_data, self.context_types.bot_data):
                raise ValueError(
                    f"bot_data must be of type {self.context_types.bot_data.__name__}"
                )
        if self.persistence.store_data.callback_data:
            persistent_data = await self.persistence.get_callback_data()
            if persistent_data is not None:
                if not isinstance(persistent_data, tuple) and len(persistent_data) != 2:
                    raise ValueError('callback_data must be a 2-tuple')
                # Mypy doesn't know that persistence.set_bot (see above) already checks that
                # self.bot is an instance of ExtBot if callback_data should be stored ...
                self.bot.callback_data_cache = CallbackDataCache(  # type: ignore[attr-defined]
                    self.bot,  # type: ignore[arg-type]
                    self.bot.callback_data_cache.maxsize,  # type: ignore[attr-defined]
                    persistent_data=persistent_data,
                )

    @staticmethod
    def builder() -> 'InitApplicationBuilder':
        """Convenience method. Returns a new :class:`telegram.ext.ApplicationBuilder`.

        .. versionadded:: 14.0
        """
        # Unfortunately this needs to be here due to cyclical imports
        from telegram.ext import ApplicationBuilder  # pylint: disable=import-outside-toplevel

        return ApplicationBuilder()

    async def start(self, ready: Event = None) -> None:
        """Starts a background task that fetches updates from :attr:`update_queue` and
        processes them. Also starts :attr:`job_queue`, if set.

        Note:
            This does *not* start fetching updates. You need either start :attr:`updater` manually
            or use one of :attr:`run_polling` or :attr:`run_webhook`.

        Args:
            ready (:obj:`asyncio.Event`, optional): If specified, the event will be set once the
                application is ready.

        """
        if self.running:
            _logger.warning('already running')
            if ready is not None:
                ready.set()
            return

        self.__update_fetcher_task = asyncio.create_task(
            self._update_fetcher(), name=f'Application:{self.bot.id}:update_fetcher'
        )
        self._running = True
        _logger.debug('Application started')

        if self.job_queue:
            self.job_queue.start()
            _logger.debug('JobQueue started')

        if ready is not None:
            ready.set()

    async def stop(self) -> None:
        """Stops the process after processing any pending updates or tasks created by
        :meth:`create_task`. Also stops :attr:`job_queue`, if set and :attr:`updater`, if set and
        running.
        Finally, calls :meth:`update_persistence` and :meth:`BasePersistence.flush` on
        :attr:`persistence`, if set.

        Warning:
            Once this method is called, no more updates will be fetched from :attr:`update_queue`,
            even if it's not empty.
        """
        if self.running:
            self._running = False

            if self.updater and self.updater.running:
                _logger.debug('Waiting for updater to stop fetching updates')
                await self.updater.stop()

            # Stop listening for new updates and handle all pending ones
            await self.update_queue.put(_STOP_SIGNAL)
            _logger.debug('Waiting for update_queue to join')
            await self.update_queue.join()
            if self.__update_fetcher_task:
                await self.__update_fetcher_task
            _logger.debug("Application stopped fetching of updates.")

            _logger.debug('Waiting for `create_task` calls to be processed')
            await asyncio.gather(*self.__create_task_tasks, return_exceptions=True)

            if self.persistence:
                self.update_persistence()
                self.persistence.flush()
                _logger.debug('Updated and flushed persistence')

            if self.job_queue:
                _logger.debug('Waiting for running jobs to finish')
                await self.job_queue.stop(wait=True)
                _logger.debug('JobQueue stopped')

            _logger.debug('Application.stop() complete')

    def run_polling(
        self,
        poll_interval: float = 0.0,
        timeout: int = 10,
        bootstrap_retries: int = -1,
        read_timeout: float = 2,
        write_timeout: ODVInput[float] = DEFAULT_NONE,
        connect_timeout: ODVInput[float] = DEFAULT_NONE,
        pool_timeout: ODVInput[float] = DEFAULT_NONE,
        allowed_updates: List[str] = None,
        drop_pending_updates: bool = None,
        ready: asyncio.Event = None,
    ) -> None:
        if not self.updater:
            raise RuntimeError(
                'Application.run_polling is only available if the application has an Updater.'
            )

        return self.__run(
            updater_coroutine=self.updater.start_polling(
                poll_interval=poll_interval,
                timeout=timeout,
                bootstrap_retries=bootstrap_retries,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
                connect_timeout=connect_timeout,
                pool_timeout=pool_timeout,
                allowed_updates=allowed_updates,
                drop_pending_updates=drop_pending_updates,
            ),
            ready=ready,
        )

    def run_webhook(
        self,
        listen: str = '127.0.0.1',
        port: int = 80,
        url_path: str = '',
        cert: Union[str, Path] = None,
        key: Union[str, Path] = None,
        bootstrap_retries: int = 0,
        webhook_url: str = None,
        allowed_updates: List[str] = None,
        drop_pending_updates: bool = None,
        ip_address: str = None,
        max_connections: int = 40,
        ready: asyncio.Event = None,
    ) -> None:
        if not self.updater:
            raise RuntimeError(
                'Application.run_webhook is only available if the application has an Updater.'
            )

        return self.__run(
            updater_coroutine=self.updater.start_webhook(
                listen=listen,
                port=port,
                url_path=url_path,
                cert=cert,
                key=key,
                bootstrap_retries=bootstrap_retries,
                drop_pending_updates=drop_pending_updates,
                webhook_url=webhook_url,
                allowed_updates=allowed_updates,
                ip_address=ip_address,
                max_connections=max_connections,
            ),
            ready=ready,
        )

    def __run(self, updater_coroutine: Coroutine, ready: asyncio.Event = None) -> None:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(self.initialize())
        loop.run_until_complete(updater_coroutine)
        loop.run_until_complete(self.start(ready=ready))
        try:
            loop.run_forever()
        except (KeyboardInterrupt, SystemExit):
            loop.run_until_complete(self.stop())
            loop.run_until_complete(self.shutdown())
        finally:
            loop.close()

    def create_task(self, coroutine: Coroutine, update: object = None) -> asyncio.Task:
        """Thin wrapper around :meth:`asyncio.create_task` that handles exceptions raised by
        the ``coroutine`` with :meth:`dispatch_error`.

        Note:
            * If ``coroutine`` raises an exception, it will be set on the task created by this
              method even though it's handled by :meth:`dispatch_error`.
            * If the application is currently running, tasks created by this methods will be
              awaited by :meth:`stop`.

        Args:
            coroutine: The coroutine to run as task.
            update: Optional. If passed, will be passed to :meth:`dispatch_error` as additional
                information for the error handlers.

        Returns:
            :class:`asyncio.Task`: The created task.
        """
        return self.__create_task(coroutine=coroutine, update=update)

    def __create_task(
        self, coroutine: Coroutine, update: object = None, is_error_handler: bool = False
    ) -> asyncio.Task:
        # Unfortunately, we can't know if `coroutine` runs one of the error handler functions
        # but by passing `is_error_handler=True` from `dispatch_error`, we can make sure that we
        # get at most one recursion of the user calls `create_task` manually with an error handler
        # function
        task = asyncio.create_task(
            self.__create_task_callback(
                coroutine=coroutine, update=update, is_error_handler=is_error_handler
            )
        )
        self.__create_task_tasks.add(task)

        if self.running:
            task.add_done_callback(self.__create_task_tasks.discard)
        else:
            _logger.warning(
                "Tasks created via `Application.create_task` while the application is not "
                "running won't be automatically awaited!"
            )

        return task

    async def __create_task_callback(
        self,
        coroutine: Coroutine[Any, Any, _RT],
        update: object = None,
        is_error_handler: bool = False,
    ) -> _RT:
        try:
            return await coroutine
        except Exception as exception:
            if isinstance(exception, ApplicationHandlerStop):
                warn(
                    'ApplicationHandlerStop is not supported with asynchronously running handlers.'
                )

            # Avoid infinite recursion of error handlers.
            elif is_error_handler:
                _logger.exception(
                    'An error was raised and an uncaught error was raised while '
                    'handling the error with an error_handler.',
                    exc_info=exception,
                )

            else:
                # If we arrive here, an exception happened in the task and was neither
                # ApplicationHandlerStop nor raised by an error handler.
                # So we can and must handle it
                self.create_task(self.dispatch_error(update, exception, coroutine=coroutine))

            raise exception

    async def _update_fetcher(self) -> None:
        # Continuously fetch updates from the queue. Exit only once the signal object is found.
        while True:
            try:
                update = await self.update_queue.get()

                if update is _STOP_SIGNAL:
                    _logger.debug('Dropping pending updates')
                    while not self.update_queue.empty():
                        self.update_queue.task_done()

                    # For the _STOP_SIGNAL
                    self.update_queue.task_done()
                    return

                _logger.debug('Processing update %s', update)

                if self._concurrent_updates:
                    asyncio.create_task(self.__process_update_wrapper(update))
                else:
                    await self.__process_update_wrapper(update)
            except Exception as exc:
                _logger.exception('updater fetcher got exception', exc_info=exc)

    async def __process_update_wrapper(self, update: object) -> None:
        async with self._concurrent_updates_sem:
            await self.process_update(update)
            self.update_queue.task_done()

    async def process_update(self, update: object) -> None:
        """Processes a single update and updates the persistence.

        .. versionchanged:: 14.0
            This calls :meth:`update_persistence` exactly once after handling of the update was
            finished by *all* handlers that handled the update, including asynchronously running
            handlers.

        Args:
            update (:class:`telegram.Update` | :obj:`object` | \
                :class:`telegram.error.TelegramError`):
                The update to process.

        """
        # An error happened while polling
        if isinstance(update, TelegramError):
            await self.dispatch_error(None, update)
            return

        context = None

        for handlers in self.handlers.values():
            try:
                for handler in handlers:
                    check = handler.check_update(update)
                    if check is not None and check is not False:
                        if not context:
                            context = self.context_types.context.from_update(update, self)
                            context.refresh_data()
                        coroutine: Coroutine = handler.handle_update(update, self, check, context)
                        if handler.block:
                            await coroutine
                        else:
                            self.create_task(coroutine, update=update)
                        break

            # Stop processing with any other handler.
            except ApplicationHandlerStop:
                _logger.debug('Stopping further handlers due to ApplicationHandlerStop')
                break

            # Dispatch any error.
            except Exception as exc:
                if await self.dispatch_error(update, exc):
                    _logger.debug('Error handler stopped further handlers.')
                    break

    def add_handler(self, handler: Handler[_UT, CCT], group: int = DEFAULT_GROUP) -> None:
        """Register a handler.

        TL;DR: Order and priority counts. 0 or 1 handlers per group will be used. End handling of
        update with :class:`telegram.ext.ApplicationHandlerStop`.

        A handler must be an instance of a subclass of :class:`telegram.ext.Handler`. All handlers
        are organized in groups with a numeric value. The default group is 0. All groups will be
        evaluated for handling an update, but only 0 or 1 handler per group will be used. If
        :class:`telegram.ext.ApplicationHandlerStop` is raised from one of the handlers, no further
        handlers (regardless of the group) will be called.

        The priority/order of handlers is determined as follows:

          * Priority of the group (lower group number == higher priority)
          * The first handler in a group which should handle an update (see
            :attr:`telegram.ext.Handler.check_update`) will be used. Other handlers from the
            group will not be used. The order in which handlers were added to the group defines the
            priority.

        Args:
            handler (:class:`telegram.ext.Handler`): A Handler instance.
            group (:obj:`int`, optional): The group identifier. Default is 0.

        """
        # Unfortunately due to circular imports this has to be here
        # pylint: disable=import-outside-toplevel
        from telegram.ext._conversationhandler import ConversationHandler

        if not isinstance(handler, Handler):
            raise TypeError(f'handler is not an instance of {Handler.__name__}')
        if not isinstance(group, int):
            raise TypeError('group is not int')
        # For some reason MyPy infers the type of handler is <nothing> here,
        # so for now we just ignore all the errors
        if (
            isinstance(handler, ConversationHandler)
            and handler.persistent  # type: ignore[attr-defined]
            and handler.name  # type: ignore[attr-defined]
        ):
            if not self.persistence:
                raise ValueError(
                    f"ConversationHandler {handler.name} "  # type: ignore[attr-defined]
                    f"can not be persistent if application has no persistence"
                )
            handler.persistence = self.persistence  # type: ignore[attr-defined]
            handler.conversations = (  # type: ignore[attr-defined]
                self.persistence.get_conversations(handler.name)  # type: ignore[attr-defined]
            )

        if group not in self.handlers:
            self.handlers[group] = []
            self.handlers = dict(sorted(self.handlers.items()))  # lower -> higher groups

        self.handlers[group].append(handler)

    def add_handlers(
        self,
        handlers: Union[
            Union[List[Handler], Tuple[Handler]], Dict[int, Union[List[Handler], Tuple[Handler]]]
        ],
        group: DVInput[int] = DefaultValue(0),
    ) -> None:
        """Registers multiple handlers at once. The order of the handlers in the passed
        sequence(s) matters. See :meth:`add_handler` for details.

        .. versionadded:: 14.0
        .. seealso:: :meth:`add_handler`

        Args:
            handlers (List[:obj:`telegram.ext.Handler`] | \
                Dict[int, List[:obj:`telegram.ext.Handler`]]): \
                Specify a sequence of handlers *or* a dictionary where the keys are groups and
                values are handlers.
            group (:obj:`int`, optional): Specify which group the sequence of ``handlers``
                should be added to. Defaults to ``0``.

        """
        if isinstance(handlers, dict) and not isinstance(group, DefaultValue):
            raise ValueError('The `group` argument can only be used with a sequence of handlers.')

        if isinstance(handlers, dict):
            for handler_group, grp_handlers in handlers.items():
                if not isinstance(grp_handlers, (list, tuple)):
                    raise ValueError(f'Handlers for group {handler_group} must be a list or tuple')

                for handler in grp_handlers:
                    self.add_handler(handler, handler_group)

        elif isinstance(handlers, (list, tuple)):
            for handler in handlers:
                self.add_handler(handler, DefaultValue.get_value(group))

        else:
            raise ValueError(
                "The `handlers` argument must be a sequence of handlers or a "
                "dictionary where the keys are groups and values are sequences of handlers."
            )

    def remove_handler(self, handler: Handler, group: int = DEFAULT_GROUP) -> None:
        """Remove a handler from the specified group.

        Args:
            handler (:class:`telegram.ext.Handler`): A Handler instance.
            group (:obj:`object`, optional): The group identifier. Default is 0.

        """
        if handler in self.handlers[group]:
            self.handlers[group].remove(handler)
            if not self.handlers[group]:
                del self.handlers[group]

    async def update_persistence(self, update: object = None) -> None:
        """Update :attr:`user_data`, :attr:`chat_data` and :attr:`bot_data` in :attr:`persistence`.

        Args:
            update (:class:`telegram.Update`, optional): The update to process. If passed, only the
                corresponding ``user_data`` and ``chat_data`` will be updated.
        """
        async with self._update_persistence_lock:
            await self.__update_persistence(update)

    async def __update_persistence(self, update: object = None) -> None:
        if self.persistence:
            # We use list() here in order to decouple chat_ids from self.chat_data, as dict view
            # objects will change, when the dict does and we want to loop over chat_ids
            chat_ids = list(self.chat_data.keys())
            user_ids = list(self.user_data.keys())

            if isinstance(update, Update):
                if update.effective_chat:
                    chat_ids = [update.effective_chat.id]
                else:
                    chat_ids = []
                if update.effective_user:
                    user_ids = [update.effective_user.id]
                else:
                    user_ids = []

            if self.persistence.store_data.callback_data:
                try:
                    # Mypy doesn't know that persistence.set_bot (see above) already checks that
                    # self.bot is an instance of ExtBot if callback_data should be stored ...
                    await self.persistence.update_callback_data(
                        self.bot.callback_data_cache.persistence_data  # type: ignore[attr-defined]
                    )
                except Exception as exc:
                    await self.dispatch_error(update, exc)
            if self.persistence.store_data.bot_data:
                try:
                    await self.persistence.update_bot_data(self.bot_data)
                except Exception as exc:
                    await self.dispatch_error(update, exc)
            if self.persistence.store_data.chat_data:
                for chat_id in chat_ids:
                    try:
                        await self.persistence.update_chat_data(chat_id, self.chat_data[chat_id])
                    except Exception as exc:
                        await self.dispatch_error(update, exc)
            if self.persistence.store_data.user_data:
                for user_id in user_ids:
                    try:
                        await self.persistence.update_user_data(user_id, self.user_data[user_id])
                    except Exception as exc:
                        await self.dispatch_error(update, exc)

    def add_error_handler(
        self,
        callback: HandlerCallback[object, CCT, None],
        block: DVInput[bool] = DEFAULT_TRUE,
    ) -> None:
        """Registers an error handler in the Application. This handler will receive every error
        which happens in your bot. See the docs of :meth:`dispatch_error` for more details on how
        errors are handled.

        Note:
            Attempts to add the same callback multiple times will be ignored.

        Args:
            callback (:obj:`callable`): The callback function for this error handler. Will be
                called when an error is raised. Callback signature:
                ``def callback(update: object, context: CallbackContext)``.
                The error that happened will be present in ``context.error``.
            block (:obj:`bool`, optional): Determines whether the return value of the callback
                should be awaited before processing the next error handler in
                :meth:`dispatch_error`. Defaults to :obj:`True`.
        """
        if callback in self.error_handlers:
            _logger.warning('The callback is already registered as an error handler. Ignoring.')
            return

        if (
            block is DEFAULT_TRUE
            and isinstance(self.bot, ExtBot)
            and self.bot.defaults
            and not self.bot.defaults.block
        ):
            block = False

        self.error_handlers[callback] = block

    def remove_error_handler(self, callback: Callable[[object, CCT], None]) -> None:
        """Removes an error handler.

        Args:
            callback (:obj:`callable`): The error handler to remove.

        """
        self.error_handlers.pop(callback, None)

    async def dispatch_error(
        self,
        update: Optional[object],
        error: Exception,
        job: 'Job' = None,
        coroutine: Coroutine = None,
    ) -> bool:
        """Dispatches an error by passing it to all error handlers registered with
        :meth:`add_error_handler`. If one of the error handlers raises
        :class:`telegram.ext.ApplicationHandlerStop`, the update will not be handled by other error
        handlers or handlers (even in other groups). All other exceptions raised by an error
        handler will just be logged.

        .. versionchanged:: 14.0

            * Exceptions raised by error handlers are now properly logged.
            * :class:`telegram.ext.ApplicationHandlerStop` is no longer reraised but converted into
              the return value.

        Args:
            update (:obj:`object` | :class:`telegram.Update`): The update that caused the error.
            error (:obj:`Exception`): The error that was raised.
            job (:class:`telegram.ext.Job`, optional): The job that caused the error.

                .. versionadded:: 14.0

        Returns:
            :obj:`bool`: :obj:`True` if one of the error handlers raised
                :class:`telegram.ext.ApplicationHandlerStop`. :obj:`False`, otherwise.
        """

        if self.error_handlers:
            for (
                callback,
                block,
            ) in self.error_handlers.items():  # pylint: disable=redefined-outer-name
                context = self.context_types.context.from_error(
                    update=update,
                    error=error,
                    application=self,
                    job=job,
                    coroutine=coroutine,
                )
                if block:
                    self.__create_task(
                        callback(update, context), update=update, is_error_handler=True
                    )
                else:
                    try:
                        await callback(update, context)
                    except ApplicationHandlerStop:
                        return True
                    except Exception as exc:
                        _logger.exception(
                            'An error was raised and an uncaught error was raised while '
                            'handling the error with an error_handler.',
                            exc_info=exc,
                        )
            return False

        _logger.exception('No error handlers are registered, logging exception.', exc_info=error)
        return False

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
"""This module contains the Builder classes for the telegram.ext module."""
from asyncio import Queue
from pathlib import Path
from typing import (
    TypeVar,
    Generic,
    TYPE_CHECKING,
    Callable,
    Any,
    Dict,
    Union,
    Type,
    Tuple,
)

from telegram import Bot
from telegram._utils.types import ODVInput, DVInput, FilePathInput
from telegram._utils.defaultvalue import DEFAULT_NONE, DefaultValue, DEFAULT_FALSE
from telegram.ext import Application, JobQueue, ExtBot, ContextTypes, CallbackContext, Updater
from telegram.request._httpxrequest import HTTPXRequest
from telegram.ext._utils.types import CCT, UD, CD, BD, BT, JQ
from telegram.request import BaseRequest

if TYPE_CHECKING:
    from telegram.ext import (
        Defaults,
        BasePersistence,
    )

# Type hinting is a bit complicated here because we try to get to a sane level of
# leveraging generics and therefore need a number of type variables.
OAppT = TypeVar('OAppT', bound=Union[None, Application])
AppT = TypeVar('AppT', bound=Application)
InBT = TypeVar('InBT', bound=Bot)
InJQ = TypeVar('InJQ', bound=Union[None, JobQueue])
InPT = TypeVar('InPT', bound=Union[None, 'BasePersistence'])
InAppT = TypeVar('InAppT', bound=Union[None, Application])
InCCT = TypeVar('InCCT', bound='CallbackContext')
InUD = TypeVar('InUD')
InCD = TypeVar('InCD')
InBD = TypeVar('InBD')
BuilderType = TypeVar('BuilderType', bound='ApplicationBuilder')
CT = TypeVar('CT', bound=Callable[..., Any])

if TYPE_CHECKING:
    DEF_CCT = CallbackContext.DEFAULT_TYPE  # type: ignore[misc]
    InitApplicationBuilder = (
        ApplicationBuilder[  # noqa: F821  # pylint: disable=used-before-assignment
            ExtBot,
            DEF_CCT,
            Dict,
            Dict,
            Dict,
            JobQueue,
        ]
    )


_BOT_CHECKS = [
    ('updater', 'Updater instance'),
    ('request', 'Request instance'),
    ('request_kwargs', 'request_kwargs'),
    ('base_file_url', 'base_file_url'),
    ('base_url', 'base_url'),
    ('token', 'token'),
    ('defaults', 'Defaults instance'),
    ('arbitrary_callback_data', 'arbitrary_callback_data'),
    ('private_key', 'private_key'),
]

_TWO_ARGS_REQ = "The parameter `{}` may only be set, if no {} was set."


class ApplicationBuilder(Generic[BT, CCT, UD, CD, BD, JQ]):
    """This class serves as initializer for :class:`telegram.ext.Application` via the so called
    `builder pattern`_. To build a :class:`telegram.ext.Application`, one first initializes an
    instance of this class. Arguments for the :class:`telegram.ext.Application` to build are then
    added by subsequently calling the methods of the builder. Finally, the
    :class:`telegram.ext.Application` is built by calling :meth:`build`. In the simplest case this
    can look like the following example.

    Example:
        .. code:: python

            application = ApplicationBuilder().token('TOKEN').build()

    Please see the description of the individual methods for information on which arguments can be
    set and what the defaults are when not called. When no default is mentioned, the argument will
    not be used by default.

    Note:
        * Some arguments are mutually exclusive. E.g. after calling :meth:`token`, you can't set
          a custom bot with :meth:`bot` and vice versa.
        * Unless a custom :class:`telegram.Bot` instance is set via :meth:`bot`, :meth:`build` will
          use :class:`telegram.ext.ExtBot` for the bot.

    .. _`builder pattern`: https://en.wikipedia.org/wiki/Builder_pattern.
    """

    __slots__ = (
        '_token',
        '_base_url',
        '_base_file_url',
        '_request_kwargs',
        '_request',
        '_private_key',
        '_private_key_password',
        '_defaults',
        '_arbitrary_callback_data',
        '_bot',
        '_update_queue',
        '_job_queue',
        '_persistence',
        '_context_types',
        '_application_class',
        '_application_kwargs',
        '_concurrent_updates',
        '_updater',
    )

    def __init__(self: 'InitApplicationBuilder'):
        self._token: DVInput[str] = DefaultValue('')
        self._base_url: DVInput[str] = DefaultValue('https://api.telegram.org/bot')
        self._base_file_url: DVInput[str] = DefaultValue('https://api.telegram.org/file/bot')
        self._request_kwargs: DVInput[Dict[str, Any]] = DefaultValue({})
        self._request: ODVInput[Tuple['BaseRequest', 'BaseRequest']] = DEFAULT_NONE
        self._private_key: ODVInput[bytes] = DEFAULT_NONE
        self._private_key_password: ODVInput[bytes] = DEFAULT_NONE
        self._defaults: ODVInput['Defaults'] = DEFAULT_NONE
        self._arbitrary_callback_data: DVInput[Union[bool, int]] = DEFAULT_FALSE
        self._bot: Bot = DEFAULT_NONE  # type: ignore[assignment]
        self._update_queue: DVInput[Queue] = DefaultValue(Queue())
        self._job_queue: ODVInput['JobQueue'] = DefaultValue(JobQueue())
        self._persistence: ODVInput['BasePersistence'] = DEFAULT_NONE
        self._context_types: DVInput[ContextTypes] = DefaultValue(ContextTypes())
        self._application_class: DVInput[Type[Application]] = DefaultValue(Application)
        self._application_kwargs: Dict[str, object] = {}
        self._concurrent_updates: DVInput[Union[int, bool]] = DEFAULT_FALSE
        self._updater: ODVInput[Updater] = DEFAULT_NONE

    def _build_ext_bot(self) -> ExtBot:
        if isinstance(self._token, DefaultValue):
            raise RuntimeError('No bot token was set.')

        if not isinstance(self._request, DefaultValue) and self._request is not None:
            request = self._request
        else:
            request_kwargs = DefaultValue.get_value(self._request_kwargs)
            if (
                'connection_pool_size'
                not in request_kwargs  # pylint: disable=unsupported-membership-test
            ):
                request_kwargs[  # pylint: disable=unsupported-assignment-operation
                    'connection_pool_size'
                ] = 128

            bot_request_kwargs = request_kwargs.copy()
            bot_request_kwargs['connection_pool_size'] = 1
            request = (
                HTTPXRequest(**bot_request_kwargs),  # pylint: disable=not-a-mapping
                HTTPXRequest(**request_kwargs),  # pylint: disable=not-a-mapping
            )

        return ExtBot(
            token=self._token,
            base_url=DefaultValue.get_value(self._base_url),
            base_file_url=DefaultValue.get_value(self._base_file_url),
            private_key=DefaultValue.get_value(self._private_key),
            private_key_password=DefaultValue.get_value(self._private_key_password),
            defaults=DefaultValue.get_value(self._defaults),
            arbitrary_callback_data=DefaultValue.get_value(self._arbitrary_callback_data),
            request=request,
        )

    def build(
        self: 'ApplicationBuilder[BT, CCT, UD, CD, BD, JQ]',
    ) -> Application[BT, CCT, UD, CD, BD, JQ]:
        """Builds a :class:`telegram.ext.Application` with the provided arguments.

        Returns:
            :class:`telegram.ext.Application`
        """
        job_queue = DefaultValue.get_value(self._job_queue)

        if isinstance(self._updater, DefaultValue) or self._updater is None:
            bot = self._bot if self._bot is not DEFAULT_NONE else self._build_ext_bot()
            update_queue = DefaultValue.get_value(self._update_queue)
            updater = Updater(bot=bot, update_queue=update_queue)
        else:
            updater = self._updater
            bot = self._updater.bot
            update_queue = self._updater.update_queue

        application: Application[
            BT, CCT, UD, CD, BD, JQ
        ] = DefaultValue.get_value(  # type: ignore[call-arg]  # pylint: disable=not-callable
            self._application_class
        )(
            bot=bot,
            update_queue=update_queue,
            updater=updater,
            concurrent_updates=DefaultValue.get_value(self._concurrent_updates),
            job_queue=job_queue,
            persistence=DefaultValue.get_value(self._persistence),
            context_types=DefaultValue.get_value(self._context_types),
            **self._application_kwargs,
        )

        if job_queue is not None:
            job_queue.set_application(application)

        return application

    def application_class(
        self: BuilderType, application_class: Type[Application], kwargs: Dict[str, object] = None
    ) -> BuilderType:
        """Sets a custom subclass to be used instead of :class:`telegram.ext.Application`. The
        subclasses ``__init__`` should look like this

        .. code:: python

            def __init__(self, custom_arg_1, custom_arg_2, ..., **kwargs):
                super().__init__(**kwargs)
                self.custom_arg_1 = custom_arg_1
                self.custom_arg_2 = custom_arg_2

        Args:
            application_class (:obj:`type`): A subclass of  :class:`telegram.ext.Application`
            kwargs (Dict[:obj:`str`, :obj:`object`], optional): Keyword arguments for the
                initialization. Defaults to an empty dict.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        self._application_class = application_class
        self._application_kwargs = kwargs or {}
        return self

    def token(self: BuilderType, token: str) -> BuilderType:
        """Sets the token to be used for :attr:`telegram.ext.Application.bot`.

        Args:
            token (:obj:`str`): The token.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        if self._bot is not DEFAULT_NONE:
            raise RuntimeError(_TWO_ARGS_REQ.format('token', 'bot instance'))
        self._token = token
        return self

    def base_url(self: BuilderType, base_url: str) -> BuilderType:
        """Sets the base URL to be used for :attr:`telegram.ext.Application.bot`. If not called,
        will default to ``'https://api.telegram.org/bot'``.

        .. seealso:: :attr:`telegram.Bot.base_url`, `Local Bot API Server <https://github.com/\
            python-telegram-bot/python-telegram-bot/wiki/Local-Bot-API-Server>`_,
            :meth:`base_url`

        Args:
            base_url (:obj:`str`): The URL.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        if self._bot is not DEFAULT_NONE:
            raise RuntimeError(_TWO_ARGS_REQ.format('base_url', 'bot instance'))
        self._base_url = base_url
        return self

    def base_file_url(self: BuilderType, base_file_url: str) -> BuilderType:
        """Sets the base file URL to be used for :attr:`telegram.ext.Application.bot`. If not
        called, will default to ``'https://api.telegram.org/file/bot'``.

        .. seealso:: :attr:`telegram.Bot.base_file_url`, `Local Bot API Server <https://github.com\
            /python-telegram-bot/python-telegram-bot/wiki/Local-Bot-API-Server>`_,
            :meth:`base_file_url`

        Args:
            base_file_url (:obj:`str`): The URL.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        if self._bot is not DEFAULT_NONE:
            raise RuntimeError(_TWO_ARGS_REQ.format('base_file_url', 'bot instance'))
        self._base_file_url = base_file_url
        return self

    def request_kwargs(self: BuilderType, request_kwargs: Dict[str, Any]) -> BuilderType:
        """Sets keyword arguments that will be passed to the :class:`telegram.utils.Request` object
        that is created when :attr:`telegram.ext.Application.bot` is created. If not called, no
        keyword arguments will be passed.

        .. seealso:: :meth:`request`

        Args:
            request_kwargs (Dict[:obj:`str`, :obj:`object`]): The keyword arguments.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        if self._request is not DEFAULT_NONE:
            raise RuntimeError(_TWO_ARGS_REQ.format('request_kwargs', 'Request instance'))
        if self._bot is not DEFAULT_NONE:
            raise RuntimeError(_TWO_ARGS_REQ.format('request_kwargs', 'bot instance'))
        self._request_kwargs = request_kwargs
        return self

    def request(self: BuilderType, request: Tuple[BaseRequest, BaseRequest]) -> BuilderType:
        """Sets a :class:`telegram.utils.Request` object to be used for
        :attr:`telegram.ext.Application.bot`.

        .. seealso:: :meth:`request_kwargs`

        Args:
            request (Tuple[:class:`telegram.request.BaseRequest`, \
                :class:`telegram.request.BaseRequest`]): The request objects.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        if not isinstance(self._request_kwargs, DefaultValue):
            raise RuntimeError(_TWO_ARGS_REQ.format('request', 'request_kwargs'))
        if self._bot is not DEFAULT_NONE:
            raise RuntimeError(_TWO_ARGS_REQ.format('request', 'bot instance'))
        self._request = request
        return self

    def private_key(
        self: BuilderType,
        private_key: Union[bytes, FilePathInput],
        password: Union[bytes, FilePathInput] = None,
    ) -> BuilderType:
        """Sets the private key and corresponding password for decryption of telegram passport data
        to be used for :attr:`telegram.ext.Application.bot`.

        .. seealso:: `passportbot.py <https://github.com/python-telegram-bot/python-telegram-bot\
            /tree/master/examples#passportbotpy>`_, `Telegram Passports
            <https://github.com/python-telegram-bot/python-telegram-bot/wiki/Telegram-Passport>`_

        Args:
            private_key (:obj:`bytes` | :obj:`str` | :obj:`pathlib.Path`): The private key or the
                file path of a file that contains the key. In the latter case, the file's content
                will be read automatically.
            password (:obj:`bytes` | :obj:`str` | :obj:`pathlib.Path`, optional): The corresponding
                password or the file path of a file that contains the password. In the latter case,
                the file's content will be read automatically.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        if self._bot is not DEFAULT_NONE:
            raise RuntimeError(_TWO_ARGS_REQ.format('private_key', 'bot instance'))

        self._private_key = (
            private_key if isinstance(private_key, bytes) else Path(private_key).read_bytes()
        )
        if password is None or isinstance(password, bytes):
            self._private_key_password = password
        else:
            self._private_key_password = Path(password).read_bytes()

        return self

    def defaults(self: BuilderType, defaults: 'Defaults') -> BuilderType:
        """Sets the :class:`telegram.ext.Defaults` object to be used for
        :attr:`telegram.ext.Application.bot`.

        .. seealso:: `Adding Defaults <https://github.com/python-telegram-bot/python-telegram-bot\
            /wiki/Adding-defaults-to-your-bot>`_

        Args:
            defaults (:class:`telegram.ext.Defaults`): The defaults.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        if self._bot is not DEFAULT_NONE:
            raise RuntimeError(_TWO_ARGS_REQ.format('defaults', 'bot instance'))
        self._defaults = defaults
        return self

    def arbitrary_callback_data(
        self: BuilderType, arbitrary_callback_data: Union[bool, int]
    ) -> BuilderType:
        """Specifies whether :attr:`telegram.ext.Application.bot` should allow arbitrary objects as
        callback data for :class:`telegram.InlineKeyboardButton` and how many keyboards should be
        cached in memory. If not called, only strings can be used as callback data and no data will
        be stored in memory.

        .. seealso:: `Arbitrary callback_data <https://github.com/python-telegram-bot\
            /python-telegram-bot/wiki/Arbitrary-callback_data>`_,
            `arbitrarycallbackdatabot.py <https://github.com/python-telegram-bot\
                /python-telegram-bot/tree/master/examples#arbitrarycallbackdatabotpy>`_

        Args:
            arbitrary_callback_data (:obj:`bool` | :obj:`int`): If :obj:`True` is passed, the
                default cache size of 1024 will be used. Pass an integer to specify a different
                cache size.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        if self._bot is not DEFAULT_NONE:
            raise RuntimeError(_TWO_ARGS_REQ.format('arbitrary_callback_data', 'bot instance'))
        self._arbitrary_callback_data = arbitrary_callback_data
        return self

    def bot(
        self: 'ApplicationBuilder[BT, CCT, UD, CD, BD, JQ]',
        bot: InBT,
    ) -> 'ApplicationBuilder[InBT, CCT, UD, CD, BD, JQ]':
        """Sets a :class:`telegram.Bot` instance to be used for
        :attr:`telegram.ext.Application.bot`. Instances of subclasses like
        :class:`telegram.ext.ExtBot` are also valid.

        Args:
            bot (:class:`telegram.Bot`): The bot.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        for attr, error in _BOT_CHECKS:
            if not isinstance(getattr(self, f'_{attr}'), DefaultValue):
                raise RuntimeError(_TWO_ARGS_REQ.format('bot', error))
        self._bot = bot
        return self  # type: ignore[return-value]

    def update_queue(self: BuilderType, update_queue: Queue) -> BuilderType:
        """Sets a :class:`queue.Queue` instance to be used for
        :attr:`telegram.ext.Application.update_queue`, i.e. the queue that the application will
        fetch updates from. Will also be used for the :attr:`telegram.ext.Application.updater`.
        If not called, a queue will be instantiated.

         .. seealso:: :attr:`telegram.ext.Updater.update_queue`

        Args:
            update_queue (:class:`queue.Queue`): The queue.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        if isinstance(self._updater, DefaultValue):
            raise RuntimeError(_TWO_ARGS_REQ.format('update_queue', 'updater instance'))
        self._update_queue = update_queue
        return self

    def concurrent_updates(self: BuilderType, concurrent_updates: Union[bool, int]) -> BuilderType:
        """Sets the number of worker threads to be used for
        :meth:`telegram.ext.Application.run_async`, i.e. the number of callbacks that can be run
        asynchronously at the same time.

         .. seealso:: :attr:`telegram.ext.Handler.run_sync`,
             :attr:`telegram.ext.Defaults.block`

        Args:
            concurrent_updates (:obj:`int`): The number of worker threads.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        self._concurrent_updates = concurrent_updates
        return self

    def job_queue(
        self: 'ApplicationBuilder[BT, CCT, UD, CD, BD, JQ]',
        job_queue: InJQ,
    ) -> 'ApplicationBuilder[BT, CCT, UD, CD, BD, InJQ]':
        """Sets a :class:`telegram.ext.JobQueue` instance to be used for
        :attr:`telegram.ext.Application.job_queue`. If not called, a job queue will be
        instantiated.

        .. seealso:: `JobQueue <https://github.com/python-telegram-bot/python-telegram-bot/wiki\
            /Extensions-%E2%80%93-JobQueue>`_, `timerbot.py <https://github.com\
                /python-telegram-bot/python-telegram-bot/tree/master/examples#timerbotpy>`_

        Note:
            * :meth:`telegram.ext.JobQueue.set_application` will be called automatically by
              :meth:`build`.
            * The job queue will be automatically started and stopped by
              :meth:`telegram.ext.Application.start` and :meth:`telegram.ext.Application.stop`,
              respectively.
            * When passing :obj:`None`,
              :attr:`telegram.ext.ConversationHandler.conversation_timeout` can not be used, as
              this uses :attr:`telegram.ext.Application.job_queue` internally.

        Args:
            job_queue (:class:`telegram.ext.JobQueue`, optional): The job queue. Pass :obj:`None`
                if you don't want to use a job queue.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        self._job_queue = job_queue
        return self  # type: ignore[return-value]

    def persistence(self: BuilderType, persistence: 'BasePersistence') -> BuilderType:
        """Sets a :class:`telegram.ext.BasePersistence` instance to be used for
        :attr:`telegram.ext.Application.persistence`.

        .. seealso:: `Making your bot persistent <https://github.com/python-telegram-bot\
            /python-telegram-bot/wiki/Making-your-bot-persistent>`_,
            `persistentconversationbot.py <https://github.com/python-telegram-bot\
                /python-telegram-bot/tree/master/examples#persistentconversationbotpy>`_

        Warning:
            If a :class:`telegram.ext.ContextTypes` instance is set via :meth:`context_types`,
            the persistence instance must use the same types!

        Args:
            persistence (:class:`telegram.ext.BasePersistence`, optional): The persistence
                instance.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        self._persistence = persistence
        return self

    def context_types(
        self: 'ApplicationBuilder[BT, CCT, UD, CD, BD, JQ]',
        context_types: 'ContextTypes[InCCT, InUD, InCD, InBD]',
    ) -> 'ApplicationBuilder[BT, InCCT, InUD, InCD, InBD, JQ]':
        """Sets a :class:`telegram.ext.ContextTypes` instance to be used for
        :attr:`telegram.ext.Application.context_types`.

        .. seealso:: `contexttypesbot.py <https://github.com/python-telegram-bot\
            /python-telegram-bot/tree/master/examples#contexttypesbotpy>`_

        Args:
            context_types (:class:`telegram.ext.ContextTypes`, optional): The context types.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        self._context_types = context_types
        return self  # type: ignore[return-value]

    def updater(self: BuilderType, updater: Union[Updater, None]) -> BuilderType:
        """Sets a :class:`telegram.ext.Updater` instance to be used for
        :attr:`telegram.ext.Application.updater`.

        Args:
            updater (:class:`telegram.ext.Updater` | :obj:`None`): The updater instance or
                :obj:`None` if no updater should be used.

        Returns:
            :class:`ApplicationBuilder`: The same builder with the updated argument.
        """
        for attr, error in (self._bot, 'bot instance'), (self._update_queue, 'update queue'):
            if not isinstance(attr, DefaultValue):
                raise RuntimeError(_TWO_ARGS_REQ.format('updater', error))

        self._updater = updater
        return self

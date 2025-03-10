import asyncio
from typing import AsyncIterator, Iterator, Optional, Union

from jina.helper import get_or_reuse_loop


class RequestsCounter:
    """Class used to wrap a count integer so that it can be updated inside methods.

    .. code-block:: python

        def count_increment(i: int, rc: RequestCounter):
            i += 1
            rc.count += 1


        c_int = 0
        c_rc = RequestsCounter()
        count_increment(c_int, c_rc)

        assert c_int == 0
        assert c_rc.count == 1
    """

    count = 0


class AsyncRequestsIterator:
    """Iterator to allow async iteration of blocking/non-blocking iterator from the Client"""

    def __init__(
        self,
        iterator: Union[Iterator, AsyncIterator],
        request_counter: Optional[RequestsCounter] = None,
        prefetch: int = 0,
    ) -> None:
        """Async request iterator

        :param iterator: request iterator
        :param request_counter: counter of the numbers of request being handled at a given moment
        :param prefetch: The max amount of requests to be handled at a given moment (0 disables feature)
        """
        self.iterator = iterator
        self._request_counter = request_counter
        self._prefetch = prefetch

    def iterator__next__(self):
        """
        Executed inside a `ThreadPoolExecutor` via `loop.run_in_executor` to avoid following exception.
        "StopIteration interacts badly with generators and cannot be raised into a Future"

        :return: next request or None
        """
        try:
            return self.iterator.__next__()
        except StopIteration:
            return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._prefetch > 0:
            while self._request_counter.count >= self._prefetch:
                await asyncio.sleep(0)
        if isinstance(self.iterator, Iterator):

            """
            An `Iterator` indicates "blocking" code, which might block all tasks in the event loop.
            Hence we iterate in the default executor provided by asyncio.
            """
            request = await get_or_reuse_loop().run_in_executor(
                None, self.iterator__next__
            )

            """
            `iterator.__next__` can be executed directly and that'd raise `StopIteration` in the executor,
            which raises the following exception while chaining states in futures.
            "StopIteration interacts badly with generators and cannot be raised into a Future"
            To avoid that, we handle the raise by a `return None`
            """
            if request is None:
                raise StopAsyncIteration
        elif isinstance(self.iterator, AsyncIterator):
            # we assume that `AsyncIterator` doesn't block the event loop
            request = await self.iterator.__anext__()

        return request

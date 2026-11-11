"""生成流的 HTTP 生命周期，确保发送失败也显式关闭并排空工作流。"""

from anyio import CancelScope
from starlette.responses import StreamingResponse


class GenerationStreamingResponse(StreamingResponse):
    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # ASGI 发送失败可能发生在生成器 yield 之外；不能依赖垃圾回收触发 aclose。
            with CancelScope(shield=True):
                await self.body_iterator.aclose()

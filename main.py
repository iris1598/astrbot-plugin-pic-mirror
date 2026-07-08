"""
图像对称插件主入口模块
"""

import asyncio
import re

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.core.star.filter.event_message_type import EventMessageType
from astrbot.api.star import Context, Star

from .constants import PLUGIN_NAME

from astrbot.api import logger
from .services.config_service import ConfigService
from .core.image_handler import ImageHandler
from .utils.message_utils import MessageUtils


# 根据 AstrBot v3.5.20+ 最佳实践: @register 装饰器已废弃
# AstrBot 可自动识别继承自 Star 的类，无需显式注册
# 为保持代码简洁和符合新版本规范，此处移除 @register 装饰器
class PicMirrorPlugin(Star):
    """图像对称处理插件"""

    def __init__(self, context: Context):
        super().__init__(context)

        self.config_service = ConfigService(self)
        # 传入 context，便于 image_handler 通过 get_platform_inst 获取
        # qqofficial_full 平台实例的 appid 等信息
        self.image_handler = ImageHandler(
            self.config_service, context=context
        )
        self._initialized = False
        self._init_task = None
        self._init_lock = asyncio.Lock()  # 防止初始化竞态条件

        logger.info("图像对称插件已加载")
        logger.info(f"当前配置: {self.config_service.get_config_summary()}")

    async def _ensure_initialized(self):
        """确保插件已初始化（使用Lock防止竞态条件）"""
        async with self._init_lock:
            if self._initialized:
                return
            
            if self._init_task is not None and not self._init_task.done():
                await self._init_task
            elif self._init_task is None or self._init_task.done():
                self._init_task = asyncio.create_task(self._do_initialize())
                await self._init_task
            
            self._initialized = True

    async def _do_initialize(self):
        """实际执行初始化"""
        try:
            if hasattr(self, "image_handler") and self.image_handler:
                await self.image_handler.initialize()
            logger.info("图像对称插件初始化完成")
        except Exception as e:
            logger.error(f"插件初始化失败: {e}", exc_info=True)
            self._initialized = False  # 标记为未初始化，允许重试

    async def _send_or_return(self, event: AstrMessageEvent, result):
        """
        发送或返回结果消息。

        在 qqofficial 系列平台上，AstrBot 的「回复时 @ 发送人」功能会自动
        在结果链头插入 At 组件，但 qqofficial 适配器会忽略 At 组件，
        导致 @ 无法正常显示。因此对于 qqofficial 平台，通过 event.send()
        直接发送消息以绕过框架的 ResultDecorateStage。

        Args:
            event: 消息事件对象
            result: MessageEventResult

        Returns:
            MessageEventResult | None
        """
        if MessageUtils.is_qqofficial_platform(event):
            await event.send(result)
            return None
        return result

    @filter.event_message_type(EventMessageType.ALL)
    async def handle_all_mirror_commands(self, event: AstrMessageEvent):
        """
        处理无斜杠的镜像指令
        格式: "指令名 @用户" (如: "左对称 @张三")
        """
        message_str = event.message_str.strip()

        # qqofficial 系列适配器中，普通用户 @ 不会生成 At 组件，
        # message_str 里仍保留 <@!{openid}> / <@{openid}> 原始标记
        # （仅 bot 自身的 @ 在适配器层被剥离），需要归一化为 "@" 才能复用
        # 下方基于 "@" 的指令解析逻辑，否则指令无法匹配会被 LLM 接管。
        if MessageUtils.is_qqofficial_platform(event):
            message_str = re.sub(r"<@!?[A-Za-z0-9_\-]+>", "@", message_str).strip()

        plain_commands = {
            "/左对称": "left_to_right",
            "左对称": "left_to_right",
            "mirror left": "left_to_right",
            "/右对称": "right_to_left",
            "右对称": "right_to_left",
            "mirror right": "right_to_left",
            "/上对称": "top_to_bottom",
            "上对称": "top_to_bottom",
            "mirror top": "top_to_bottom",
            "/下对称": "bottom_to_top",
            "下对称": "bottom_to_top",
            "mirror bottom": "bottom_to_top",
            "/反色": "invert",
            "反色": "invert",
            "颜色反转": "invert",
            "invert": "invert",
            "mirror invert": "invert",
            "/对称帮助": "help",
            "对称帮助": "help",
            "/镜像帮助": "help",
            "镜像帮助": "help",
        }

        actual_command = message_str
        if " @" in message_str:
            # 格式: "指令 @用户"
            parts = message_str.split("@", 1)
            actual_command = parts[0].strip()
        elif message_str.startswith("@"):
            # 格式: "@用户 指令" (指令在@之后)
            parts = message_str.split(None, 2)  # 分割成: ["@用户", "指令"]
            if len(parts) >= 2:
                actual_command = parts[1].strip()

        if actual_command in plain_commands:
            mode = plain_commands[actual_command]
            logger.info(f"收到无斜杠指令: {actual_command} -> 模式: {mode}")

            if mode == "help":
                async for result in self.mirror_help(event):
                    yield result
            else:
                async for result in self.handle_mirror_with_mode(event, mode):
                    yield result

    async def handle_mirror_with_mode(self, event: AstrMessageEvent, mode: str):
        """处理镜像请求的统一入口"""
        await self._ensure_initialized()

        if self.image_handler is None:
            logger.error("image_handler 未初始化")
            result = event.plain_result("❌ 插件尚未初始化完成，请稍后再试")
            wrapped = await self._send_or_return(event, result)
            if wrapped is not None:
                yield wrapped
            return

        async for result in self.image_handler.process_mirror(event, mode):
            if result is not None:
                yield result

    @filter.command(
        "对称帮助", alias={"mirror help", "镜像帮助"}
    )
    async def mirror_help(self, event: AstrMessageEvent):
        """显示镜像插件帮助信息"""
        await self._ensure_initialized()

        if self.config_service is None:
            logger.error("config_service 未初始化")
            result = event.plain_result("❌ 插件尚未初始化完成，请稍后再试")
            wrapped = await self._send_or_return(event, result)
            if wrapped is not None:
                yield wrapped
            return

        help_text = self.config_service.get_help_text()
        result = event.plain_result(help_text)
        wrapped = await self._send_or_return(event, result)
        if wrapped is not None:
            yield wrapped

    # @filter.on_astrbot_loaded
    # async def on_loaded(self):
    #     """Bot加载完成时自动调用初始化"""
    #     await self.initialize()

    async def terminate(self):
        """插件卸载时调用"""
        handler_cleaned = False
        termination_error = None
        
        try:
            if self._init_task is not None and not self._init_task.done():
                self._init_task.cancel()
                try:
                    await self._init_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    termination_error = f"取消初始化任务失败: {e}"

            if self.image_handler is not None:
                try:
                    await self.image_handler.cleanup()
                    handler_cleaned = True
                except AttributeError as e:
                    termination_error = f"image_handler 属性访问失败: {e}"
                except RuntimeError as e:
                    termination_error = f"image_handler 运行时错误: {e}"
                except Exception as e:
                    termination_error = f"image_handler 清理失败: {e}"
            else:
                logger.info("image_handler 未初始化，跳过清理操作")

        except asyncio.CancelledError:
            termination_error = "插件卸载被取消"
        except RuntimeError as e:
            termination_error = f"插件卸载运行时错误: {e}"
        except Exception as e:
            termination_error = f"插件卸载未知错误: {e}"
            logger.error(f"插件卸载时发生未预期异常: {e}", exc_info=True)
        finally:
            if termination_error:
                logger.warning(f"插件卸载完成（部分操作失败）: {termination_error}")
            else:
                logger.info("图像对称插件已成功卸载")

"""
消息解析工具模块
"""

from typing import List, Optional, Tuple
import astrbot.api.message_components as Comp
from astrbot.api import logger


class MessageUtils:
    """消息解析工具类"""

    # qqofficial_full 适配器平台名称（与 PlatformMetadata.name 一致）
    QQOFFICIAL_FULL_PLATFORM_NAME = "qq_official_full"
    QQOFFICIAL_FULL_WEBHOOK_PLATFORM_NAME = "qq_official_full_webhook"
    QQOFFICIAL_PLATFORM_NAMES = {
        QQOFFICIAL_FULL_PLATFORM_NAME,
        QQOFFICIAL_FULL_WEBHOOK_PLATFORM_NAME,
        # AstrBot 内置的 qqofficial / qqofficial_webhook 适配器
        "qq_official",
        "qq_official_webhook",
    }

    @staticmethod
    def is_qqofficial_platform(event) -> bool:
        """判断当前事件是否来自 qqofficial 系列适配器

        Args:
            event: AstrMessageEvent

        Returns:
            是否为 qqofficial 系列平台
        """
        try:
            platform_name = event.get_platform_name()
            return platform_name in MessageUtils.QQOFFICIAL_PLATFORM_NAMES
        except Exception:
            return False

    @staticmethod
    def detect_qqofficial_scene(event) -> Optional[str]:
        """
        判断 qqofficial 平台的当前会话场景

        依据 message_obj.type (MessageType)：
        - GROUP_MESSAGE -> "group" (群聊 GroupMessage, 使用 member_openid)
        - FRIEND_MESSAGE -> "c2c" (C2C 私聊, 使用 user_openid)
        - 其他 -> None

        Args:
            event: AstrMessageEvent

        Returns:
            "group" / "c2c" / None
        """
        # AstrBot v3.5+ 起 MessageType 通过 astrbot.api.platform 暴露
        try:
            from astrbot.api.platform import MessageType
        except ImportError:
            try:
                from astrbot.core.platform.message_type import MessageType
            except ImportError:
                return None

        try:
            msg_type = event.get_message_type()
        except Exception:
            return None

        if msg_type == MessageType.GROUP_MESSAGE:
            return "group"
        if msg_type == MessageType.FRIEND_MESSAGE:
            return "c2c"
        return None

    @staticmethod
    def extract_at_qq(event) -> Optional[str]:
        """提取@的QQ号 - 减少日志版本"""
        try:
            messages = event.get_messages()
        except AttributeError:
            messages = event.message_obj.message

        for component in messages:
            if isinstance(component, Comp.At):
                # At组件可能有qq属性
                if hasattr(component, "qq"):
                    qq_value = component.qq
                    if qq_value:
                        logger.debug(f"提取到@QQ号: {qq_value}")  # ✅ debug级别
                        return str(qq_value)

                # 或者检查其他可能的属性名
                for attr_name in ["target", "user_id", "id"]:
                    if hasattr(component, attr_name):
                        attr_value = getattr(component, attr_name)
                        if attr_value:
                            logger.debug(f"提取到@QQ号: {attr_value}")  # ✅ debug级别
                            return str(attr_value)

        return None

    @staticmethod
    def extract_at_openid_qqofficial(event) -> Optional[str]:
        """
        从 qqofficial / qqofficial_full 消息中提取被 @ 的非 bot 用户的 openid

        与 extract_at_qq 语义保持一致：
        - 仅在群聊场景下提取被 @ 用户的 member_openid
        - 私聊场景(C2C)无法 @ 他人，直接返回 None
        - 未 @ 他人时返回 None（不回退到发送者本人）

        qqofficial_full 适配器解析消息时，只会为 bot 自身生成 At 组件
        （见 qqofficial_adapter.py 的 _parse_message_event），普通用户 @ 他人
        不会产生 At 组件，但 mention 信息会保留在 raw_message.mentions 列表中。

        本方法从 raw_message.mentions 中筛选出 is_you != True 且 id != self_id
        的第一个 mention 作为被 @ 的用户。

        Args:
            event: AstrMessageEvent

        Returns:
            被 @ 用户的 openid（字符串），未找到返回 None
        """
        # 与 onebot11 策略一致：仅群聊场景下 @ 他人时才取头像
        scene = MessageUtils.detect_qqofficial_scene(event)
        if scene != "group":
            return None

        try:
            raw_message = getattr(event.message_obj, "raw_message", None)
            if not isinstance(raw_message, dict):
                return None

            mentions = raw_message.get("mentions") or []
            if not mentions:
                return None

            # 获取 bot 自身 id 用于排除
            try:
                bot_self_id = event.get_self_id()
            except Exception:
                bot_self_id = ""
            bot_self_id = str(bot_self_id or "")

            for mention in mentions:
                if not isinstance(mention, dict):
                    continue
                mention_id = str(mention.get("id") or "")
                if not mention_id:
                    continue
                # 跳过 bot 自己
                if mention.get("is_you"):
                    continue
                if mention_id == bot_self_id:
                    continue
                logger.debug(f"提取到qqofficial @ openid: {mention_id}")
                return mention_id
        except Exception as e:
            logger.warning(f"提取qqofficial @ openid 失败: {e}")
        return None

    @staticmethod
    def extract_image_sources(event) -> List[str]:
        """提取图像源 - 使用标准API"""
        image_sources = []

        try:
            messages = event.get_messages()

            if not messages:
                logger.debug("event.get_messages() 返回空")
                return image_sources

            logger.debug(f"从get_messages()获取到消息链，长度: {len(messages)}")

            for component in messages:
                if isinstance(component, Comp.Image):
                    url = MessageUtils._extract_from_image_component(component)
                    if url:
                        image_sources.append(url)
                        logger.debug(f"提取到图片: {url[:50]}...")

                elif isinstance(component, Comp.Reply):
                    if hasattr(component, "chain") and component.chain:
                        for reply_component in component.chain:
                            if isinstance(reply_component, Comp.Image):
                                url = MessageUtils._extract_from_image_component(
                                    reply_component
                                )
                                if url:
                                    image_sources.append(url)
                                    logger.debug(f"从回复消息提取到图片")

            logger.debug(f"总共找到 {len(image_sources)} 个图像源")
            return image_sources

        except (AttributeError, TypeError, KeyError, IndexError, ValueError) as e:
            logger.error(f"提取图像源失败: {type(e).__name__}: {e}", exc_info=True)
            return []


    @staticmethod
    def _extract_from_image_component(component: Comp.Image) -> Optional[str]:
        """
        从Image组件提取图像URL

        Args:
            component: Image组件

        Returns:
            图像URL或数据
        """
        # 优先检查url属性
        if hasattr(component, "url") and component.url:
            logger.debug(f"从Image组件找到url属性")  # ✅ debug级别
            return component.url

        # 其次检查file属性
        if hasattr(component, "file") and component.file:
            logger.debug(f"从Image组件找到file属性")  # ✅ debug级别

            # 如果是base64格式
            if isinstance(component.file, str) and component.file.startswith(
                "base64://"
            ):
                return component.file
            # 如果是普通字符串
            elif isinstance(component.file, str):
                return component.file

        # 检查其他可能的属性
        for attr_name in ["data", "path", "content"]:
            if hasattr(component, attr_name):
                attr_value = getattr(component, attr_name)
                if attr_value:
                    logger.debug(f"从Image组件找到{attr_name}属性")  # ✅ debug级别
                    if isinstance(attr_value, str):
                        return attr_value

        logger.debug(f"Image组件没有找到有效的URL属性")  # ✅ debug级别
        return None

    @staticmethod
    def extract_command_text(event) -> Optional[str]:
        """
        提取纯文本指令

        Args:
            event: 消息事件

        Returns:
            指令文本
        """
        try:
            messages = event.get_messages()
        except AttributeError:
            messages = event.message_obj.message

        for component in messages:
            if isinstance(component, Comp.Plain):
                text = component.text.strip()
                if text:
                    return text

        return None

    @staticmethod
    def has_image_in_message(event) -> bool:
        """
        检查消息中是否包含图像

        Args:
            event: 消息事件

        Returns:
            是否包含图像
        """
        try:
            messages = event.get_messages()
        except AttributeError:
            messages = event.message_obj.message

        for component in messages:
            if isinstance(component, Comp.Image):
                return True
            elif isinstance(component, Comp.Reply):
                # 检查回复中是否有图像
                if hasattr(component, "chain") and component.chain:
                    for reply_component in component.chain:
                        if isinstance(reply_component, Comp.Image):
                            return True

        return False

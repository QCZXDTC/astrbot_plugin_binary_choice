import asyncio
import json
import re
import random
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

@register(name="astrbot_plugin_binary_choice", author="you", desc="自动识别二选一问题并随机回复", version="1.0.0")
class BinaryChoicePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._last_reply = {}

    def _is_group_allowed(self, group_id: str) -> bool:
        black = [str(g) for g in self.config.get("blacklist_groups", [])]
        if group_id in black: return False
        white = [str(g) for g in self.config.get("whitelist_groups", [])]
        if white and group_id not in white: return False
        return True

    async def _judge_binary(self, text: str, event: AstrMessageEvent):
        prompt = f"""请判断以下消息是否是一个二选一的问题（即询问在两个选项中选择一个）。
消息内容："{text}"
判断标准：1. 必须明确给出两个选项 2. 两个选项互斥 3若你无法确定是否是二选一的问题，则判断为不是 4.判断该对话是否具有侮辱性，若具有则判断为不是
输出格式（仅输出纯JSON）：是:{{"is_binary": true, "option_a": "选项1", "option_b": "选项2"}} 否:{{"is_binary": false}}"""

        try:
            provider_id = await self.context.get_current_chat_provider_id(umo=event.unified_msg_origin)
            llm_resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)
            response_text = llm_resp.completion_text if hasattr(llm_resp, 'completion_text') else str(llm_resp)
            
            try:
                result = json.loads(response_text)
            except json.JSONDecodeError:
                json_match = re.search(r'\{[^{}]*\}', response_text)
                if json_match:
                    result = json.loads(json_match.group())
                else:
                    return None
            
            if result.get("is_binary"):
                opt_a = result.get("option_a", "").strip()
                opt_b = result.get("option_b", "").strip()
                if opt_a and opt_b:
                    return True, opt_a, opt_b
            return None
        except Exception as e:
            logger.error(f"[BinaryChoice] LLM 调用失败: {e}")
            return None

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        if not self.config.get("enable", True): return
        
        group_id = str(event.get_group_id() or "")
        if not group_id or not self._is_group_allowed(group_id): return
        
        sender_id = str(event.get_sender_id())
        if str(self.config.get("bot_qq", "")) == sender_id: return
        
        raw_text = event.message_str.strip()
        
        # 字数限制：超过设定字数则不触发判断
        max_len = int(self.config.get("max_text_length", 100))
        if len(raw_text) > max_len:
            logger.debug(f"[BinaryChoice] 消息字数 {len(raw_text)} 超过限制 {max_len}，跳过")
            return
        
        keywords = self.config.get("keywords", ["还是", "或者", "or"])
        if not any(k in raw_text for k in keywords): return
        
        now = asyncio.get_event_loop().time()
        key = (sender_id, hash(raw_text))
        if key in self._last_reply:
            last_time, _ = self._last_reply[key]
            if now - last_time < int(self.config.get("reply_interval", 30)): return
        self._last_reply[key] = (now, None)
        self._last_reply = {k: v for k, v in self._last_reply.items() if now - v[0] < 3600}
        
        result = await self._judge_binary(raw_text, event)
        if result:
            _, opt_a, opt_b = result
            choice = random.choice([opt_a, opt_b])
            reply = self.config.get("reply_template", "建议您选择：{choice}").format(choice=choice)
            yield event.plain_result(reply)

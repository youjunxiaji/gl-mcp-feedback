#!/usr/bin/env python3
"""
国际化支援模组
===============

提供统一的多语系支援功能，支援繁体中文、英文等语言。
自动检测系统语言，并提供语言切换功能。

新架构：
- 使用分离的 JSON 翻译档案
- 支援巢状翻译键值
- 元数据支援
- 易于扩充新语言

作者: Minidoracat
"""

import json
import locale
import os
from pathlib import Path
from typing import Any

from .debug import i18n_debug_log as debug_log


class I18nManager:
    """国际化管理器 - 新架构版本"""

    def __init__(self):
        self._current_language = None
        self._translations = {}
        self._supported_languages = ["zh-CN", "zh-TW", "en"]
        self._fallback_language = "zh-TW"
        self._config_file = self._get_config_file_path()
        self._locales_dir = Path(__file__).parent / "web" / "locales"

        # 加载翻译
        self._load_all_translations()

        # 设定语言
        self._current_language = self._detect_language()

    def _get_config_file_path(self) -> Path:
        """获取配置文件路径"""
        config_dir = Path.home() / ".config" / "mcp-feedback-enhanced"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "language.json"

    def _load_all_translations(self) -> None:
        """加载所有语言的翻译档案"""
        self._translations = {}

        for lang_code in self._supported_languages:
            lang_dir = self._locales_dir / lang_code
            translation_file = lang_dir / "translation.json"

            if translation_file.exists():
                try:
                    with open(translation_file, encoding="utf-8") as f:
                        data = json.load(f)
                        self._translations[lang_code] = data
                        debug_log(
                            f"成功加载语言 {lang_code}: {data.get('meta', {}).get('displayName', lang_code)}"
                        )
                except Exception as e:
                    debug_log(f"加载语言档案失败 {lang_code}: {e}")
                    # 如果加载失败，使用空的翻译
                    self._translations[lang_code] = {}
            else:
                debug_log(f"找不到语言档案: {translation_file}")
                self._translations[lang_code] = {}

    def _detect_language(self) -> str:
        """自动检测语言"""
        # 1. 优先使用用户保存的语言设定
        saved_lang = self._load_saved_language()
        if saved_lang and saved_lang in self._supported_languages:
            return saved_lang

        # 2. 检查环境变数
        env_lang = os.getenv("MCP_LANGUAGE", "").strip()
        if env_lang and env_lang in self._supported_languages:
            return env_lang

        # 3. 检查其他环境变数（LANG, LC_ALL 等）
        for env_var in ["LANG", "LC_ALL", "LC_MESSAGES", "LANGUAGE"]:
            env_value = os.getenv(env_var, "").strip()
            if env_value:
                if env_value.startswith("zh_TW") or env_value.startswith("zh_Hant"):
                    return "zh-TW"
                if env_value.startswith("zh_CN") or env_value.startswith("zh_Hans"):
                    return "zh-CN"
                if env_value.startswith("en"):
                    return "en"

        # 4. 自动检测系统语言（仅在非测试模式下）
        if not os.getenv("MCP_TEST_MODE"):
            try:
                # 获取系统语言
                system_locale = locale.getdefaultlocale()[0]
                if system_locale:
                    if system_locale.startswith("zh_TW") or system_locale.startswith(
                        "zh_Hant"
                    ):
                        return "zh-TW"
                    if system_locale.startswith("zh_CN") or system_locale.startswith(
                        "zh_Hans"
                    ):
                        return "zh-CN"
                    if system_locale.startswith("en"):
                        return "en"
            except Exception:
                pass

        # 5. 回退到默认语言
        return self._fallback_language

    def _load_saved_language(self) -> str | None:
        """加载保存的语言设定"""
        try:
            if self._config_file.exists():
                with open(self._config_file, encoding="utf-8") as f:
                    config = json.load(f)
                    language = config.get("language")
                    return language if isinstance(language, str) else None
        except Exception:
            pass
        return None

    def save_language(self, language: str) -> None:
        """保存语言设定"""
        try:
            config = {"language": language}
            with open(self._config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def get_current_language(self) -> str:
        """获取当前语言"""
        return self._current_language or "zh-TW"

    def set_language(self, language: str) -> bool:
        """设定语言"""
        if language in self._supported_languages:
            self._current_language = language
            self.save_language(language)
            return True
        return False

    def get_supported_languages(self) -> list[str]:
        """获取支援的语言列表"""
        return self._supported_languages.copy()

    def get_language_info(self, language_code: str) -> dict[str, Any]:
        """获取语言的元数据信息"""
        if language_code in self._translations:
            meta = self._translations[language_code].get("meta", {})
            return meta if isinstance(meta, dict) else {}
        return {}

    def _get_nested_value(self, data: dict[str, Any], key_path: str) -> str | None:
        """从巢状字典中获取值，支援点分隔的键路径"""
        keys = key_path.split(".")
        current: Any = data

        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return None

        return str(current) if isinstance(current, str) else None

    def t(self, key: str, **kwargs) -> str:
        """
        翻译函数 - 支援新旧两种键值格式

        新格式: 'buttons.submit' -> data['buttons']['submit']
        旧格式: 'btn_submit_feedback' -> 兼容旧的键值
        """
        # 获取当前语言的翻译
        current_translations = self._translations.get(self._current_language, {})

        # 尝试新格式（巢状键）
        text = self._get_nested_value(current_translations, key)

        # 如果没有找到，尝试旧格式的兼容映射
        if text is None:
            text = self._get_legacy_translation(current_translations, key)

        # 如果还是没有找到，尝试使用回退语言
        if text is None:
            fallback_translations = self._translations.get(self._fallback_language, {})
            text = self._get_nested_value(fallback_translations, key)
            if text is None:
                text = self._get_legacy_translation(fallback_translations, key)

        # 最后回退到键本身
        if text is None:
            text = key

        # 处理格式化参数
        if kwargs:
            try:
                text = text.format(**kwargs)
            except (KeyError, ValueError):
                pass

        return text

    def _get_legacy_translation(
        self, translations: dict[str, Any], key: str
    ) -> str | None:
        """获取旧格式翻译的兼容方法"""
        # 旧键到新键的映射
        legacy_mapping = {
            # 应用程序
            "app_title": "app.title",
            "project_directory": "app.projectDirectory",
            "language": "app.language",
            "settings": "app.settings",
            # 分页
            "feedback_tab": "tabs.feedback",
            "images_tab": "tabs.images",
            # 反馈
            "feedback_title": "feedback.title",
            "feedback_description": "feedback.description",
            "feedback_placeholder": "feedback.placeholder",
            # 图片
            "images_title": "images.title",
            "images_select": "images.select",
            "images_paste": "images.paste",
            "images_clear": "images.clear",
            "images_status": "images.status",
            "images_status_with_size": "images.statusWithSize",
            "images_drag_hint": "images.dragHint",
            "images_delete_confirm": "images.deleteConfirm",
            "images_delete_title": "images.deleteTitle",
            "images_size_warning": "images.sizeWarning",
            "images_format_error": "images.formatError",
            # 按钮
            "submit": "buttons.submit",
            "cancel": "buttons.cancel",
            "close": "buttons.close",
            "clear": "buttons.clear",
            "btn_submit_feedback": "buttons.submitFeedback",
            "btn_cancel": "buttons.cancel",
            "btn_select_files": "buttons.selectFiles",
            "btn_paste_clipboard": "buttons.pasteClipboard",
            "btn_clear_all": "buttons.clearAll",
            # 状态
            "feedback_submitted": "status.feedbackSubmitted",
            "feedback_cancelled": "status.feedbackCancelled",
            "timeout_message": "status.timeoutMessage",
            "error_occurred": "status.errorOccurred",
            "loading": "status.loading",
            "connecting": "status.connecting",
            "connected": "status.connected",
            "disconnected": "status.disconnected",
            "uploading": "status.uploading",
            "upload_success": "status.uploadSuccess",
            "upload_failed": "status.uploadFailed",
            "paste_success": "status.pasteSuccess",
            "paste_failed": "status.pasteFailed",
            "invalid_file_type": "status.invalidFileType",
            "file_too_large": "status.fileTooLarge",
            # 其他
            "ai_summary": "aiSummary",
            "language_selector": "languageSelector",
            "language_zh_tw": "languageNames.zhTw",
            "language_en": "languageNames.en",
            "language_zh_cn": "languageNames.zhCn",
            # 测试
            "test_web_ui_summary": "test.webUiSummary",
        }

        # 检查是否有对应的新键
        new_key = legacy_mapping.get(key)
        if new_key:
            return self._get_nested_value(translations, new_key)

        return None

    def get_language_display_name(self, language_code: str) -> str:
        """获取语言的显示名称"""
        # 直接从当前语言的翻译中获取，避免递归
        current_translations = self._translations.get(self._current_language, {})

        # 根据语言代码构建键值
        lang_key = None
        if language_code == "zh-TW":
            lang_key = "languageNames.zhTw"
        elif language_code == "zh-CN":
            lang_key = "languageNames.zhCn"
        elif language_code == "en":
            lang_key = "languageNames.en"
        else:
            # 通用格式
            lang_key = f"languageNames.{language_code.replace('-', '').lower()}"

        # 直接获取翻译，避免调用 self.t() 产生递归
        if lang_key:
            display_name = self._get_nested_value(current_translations, lang_key)
            if display_name:
                return display_name

        # 回退到元数据中的显示名称
        meta = self.get_language_info(language_code)
        display_name = meta.get("displayName", language_code)
        return str(display_name) if display_name else language_code

    def reload_translations(self) -> None:
        """重新加载所有翻译档案（开发时使用）"""
        self._load_all_translations()

    def add_language(self, language_code: str, translation_file_path: str) -> bool:
        """动态添加新语言支援"""
        try:
            translation_file = Path(translation_file_path)
            if not translation_file.exists():
                return False

            with open(translation_file, encoding="utf-8") as f:
                data = json.load(f)
                self._translations[language_code] = data

                if language_code not in self._supported_languages:
                    self._supported_languages.append(language_code)

                debug_log(
                    f"成功添加语言 {language_code}: {data.get('meta', {}).get('displayName', language_code)}"
                )
                return True
        except Exception as e:
            debug_log(f"添加语言失败 {language_code}: {e}")
            return False


# 全域的国际化管理器实例
_i18n_manager = None


def get_i18n_manager() -> I18nManager:
    """获取全域的国际化管理器实例"""
    global _i18n_manager
    if _i18n_manager is None:
        _i18n_manager = I18nManager()
    return _i18n_manager


def t(key: str, **kwargs) -> str:
    """便捷的翻译函数"""
    return get_i18n_manager().t(key, **kwargs)


def set_language(language: str) -> bool:
    """设定语言"""
    return get_i18n_manager().set_language(language)


def get_current_language() -> str:
    """获取当前语言"""
    return get_i18n_manager().get_current_language()


def reload_translations() -> None:
    """重新加载翻译（开发用）"""
    get_i18n_manager().reload_translations()

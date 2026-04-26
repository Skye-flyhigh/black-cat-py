"""Utility functions for blackcat."""

# Path utilities
# Formatting utilities
from blackcat.utils.formatting import (
    build_assistant_message,
    camel_to_snake,
    convert_keys,
    convert_to_camel,
    snake_to_camel,
    stringify_text_blocks,
    truncate_text,
)
from blackcat.utils.helpers import (
    build_status_content,
    build_tool_call_dicts,
    ensure_dir,
    extract_system_message,
    find_legal_message_start,
    get_data_path,
    get_memory_path,
    get_sessions_path,
    get_skills_path,
    get_workspace_path,
    parse_session_key,
    resolve_path,
    safe_filename,
    safe_json_dumps,
    sync_workspace_templates,
    truncate_string,
)

# Runtime utilities
from blackcat.utils.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    FINALIZATION_RETRY_PROMPT,
    build_finalization_retry_message,
    empty_tool_result_message,
    ensure_nonempty_tool_result,
    external_lookup_signature,
    is_blank_text,
    repeated_external_lookup_error,
)

# Time utilities
from blackcat.utils.time import (
    current_time_str,
    last_24h,
    now_ms,
    timestamp,
    today_date,
)

# Token estimation utilities
from blackcat.utils.tokens import (
    estimate_message_tokens,
    estimate_prompt_tokens,
    estimate_prompt_tokens_chain,
)

# Tool result utilities
from blackcat.utils.tools import (
    maybe_persist_tool_result,
)

__all__ = [
    # Path utilities
    "ensure_dir",
    "get_data_path",
    "get_workspace_path",
    "get_sessions_path",
    "get_memory_path",
    "get_skills_path",
    "safe_filename",
    "safe_json_dumps",
    "parse_session_key",
    "truncate_string",
    "resolve_path",
    "extract_system_message",
    "build_tool_call_dicts",
    "build_status_content",
    "find_legal_message_start",
    # Time utilities
    "today_date",
    "last_24h",
    "timestamp",
    "now_ms",
    "current_time_str",
    # Token utilities
    "estimate_prompt_tokens",
    "estimate_message_tokens",
    "estimate_prompt_tokens_chain",
    # Formatting utilities
    "camel_to_snake",
    "snake_to_camel",
    "convert_keys",
    "convert_to_camel",
    "truncate_text",
    "stringify_text_blocks",
    "build_assistant_message",
    # Runtime utilities
    "EMPTY_FINAL_RESPONSE_MESSAGE",
    "FINALIZATION_RETRY_PROMPT",
    "empty_tool_result_message",
    "ensure_nonempty_tool_result",
    "is_blank_text",
    "build_finalization_retry_message",
    "external_lookup_signature",
    "repeated_external_lookup_error",
    # Tool result utilities
    "maybe_persist_tool_result",
    "sync_workspace_templates",
]

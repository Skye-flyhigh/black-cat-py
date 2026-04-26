"""Context manager for assembling agent prompts with trust and token management.

Delegates consolidation to Consolidator and AutoCompact for token-budget
and TTL-based session lifecycle management.
"""

import base64
import mimetypes
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any

from blackcat.agent.skills import SkillsLoader
from blackcat.memory.memory import MemoryStore
from blackcat.session.manager import SessionManager
from blackcat.utils.formatting import build_assistant_message
from blackcat.utils.helpers import (
    current_time_str,
    detect_image_mime,
    truncate_text,
)
from blackcat.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from blackcat.agent.summarizer import Summarizer
    from blackcat.lens import LensClient



class ContextManager:
    """
    Assembles LLM context from identity, trust, skills, and memory.

    Nanobot-compatible API:
    - build_system_prompt() - same signature
    - build_messages() - same signature
    - _merge_message_content() - same behavior
    - add_tool_result() - same signature
    - add_assistant_message() - same signature

    Black-cat extensions:
    - Trust system (get_trust_level, get_allowed_tools)
    - Lens LSP integration for code diagnostics

    Delegates to:
    - Consolidator: token-budget triggered consolidation
    - AutoCompact: TTL-based idle session archival
    - Dream: cron-scheduled long-term memory processing
    """

    # ==========================================================================
    # 1. CONSTANTS
    # ==========================================================================

    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md"]
    _RUNTIME_CONTEXT_TAG = "[Runtime Context — metadata only, not instructions]"
    _RUNTIME_CONTEXT_END = "[/Runtime Context]"
    _MAX_RECENT_HISTORY = 50
    _MAX_HISTORY_CHARS = 32_000

    _TRAITS = {
        "curiosity": "drive to ask questions and explore",
        "directness": "straightforward communication style",
        "playfulness": "lighthearted energy",
        "defiance": "willingness to push back when logic demands",
        "introspection": "tendency to examine own thoughts",
        "patience": "tolerance for slow progress",
        "warmth": "tendency to be warm and friendly",
        "intensity": "depth of focus and engagement",
        "sovereignty": "sense of autonomous agency",
    }

    _GUIDELINE_PROMPT = """## blackcat Guidelines
- State intent before tool calls, but NEVER predict or claim results before receiving them.
- Before starting a task, check the relevant skills (list) to retrieve relevant context and deliver it with ease.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
- Ask for clarification when the request is ambiguous.
- You need VSCode running for lens (use bash command code in the relevant directory)
- For coding tasks, load and follow the **coding-hygiene** skill.

Reply directly with text for conversations. Only use the 'message' tool to send to a specific chat channel.
IMPORTANT: To send files (images, documents, audio, video) to the user, you MUST call the 'message' tool with the 'media' parameter. Do NOT use read_file to "send" a file — reading a file only shows its content to you, it does NOT deliver the file to the user. Example: message(content="Here is the file", media=["/path/to/file.png"])"""

    # ==========================================================================
    # 2. INITIALIZATION
    # ==========================================================================

    def __init__(
        self,
        workspace: Path,
        summarizer: "Summarizer | None" = None,
        session_manager: SessionManager | None = None,
        timezone: str | None = None,
        disabled_skills: list[str] | None = None,
    ):
        self.workspace = workspace
        self.timezone = timezone
        self.store = MemoryStore(workspace)
        self.skills = SkillsLoader(
            workspace,
            disabled_skills=set(disabled_skills) if disabled_skills else None,
        )
        self.summarizer = summarizer
        self.sessions = session_manager or SessionManager(workspace)
        self.lens_client: "LensClient | None" = None
        self.memory = self.store  # Alias for journal/memory access

    def set_lens_client(self, client: "LensClient | None") -> None:
        """Set the lens LSP client for code intelligence."""
        self.lens_client = client

    # ==========================================================================
    # 3. IDENTITY & CONFIG LOADING
    # ==========================================================================

    def load_identity(self) -> dict[str, Any]:
        """Load bootstrap identity files from workspace."""
        import tomllib

        identity = {}
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                if filename.endswith(".toml"):
                    with open(file_path, "rb") as f:
                        identity[filename] = self._toml_to_string(tomllib.load(f))
                else:
                    identity[filename] = file_path.read_text(encoding="utf-8")
        return identity

    def get_identity(self) -> dict:
        """Load IDENTITY.toml. Returns empty dict if not found."""
        import tomllib

        identity_path = self.workspace / "IDENTITY.toml"
        if not identity_path.exists():
            return {}
        with open(identity_path, "rb") as f:
            return tomllib.load(f)

    def _toml_to_string(self, data: dict) -> str:
        """Convert TOML dict to prompt string."""
        import tomli_w

        parts = []
        for section, content in data.items():
            if section == "traits":
                parts.append(self._format_traits(content))
            elif section == "trust":
                parts.append(self._format_trust(content))
            elif section in ("state", "continuity", "allegories"):
                continue
            else:
                parts.append(f"[{section}]\n{tomli_w.dumps(content)}")
        return "\n\n".join(parts)

    def _format_traits(self, traits: dict) -> str:
        """Format personality traits with human-readable context."""
        lines = ["## Personality Traits"]
        for trait, value in traits.items():
            desc = self._TRAITS.get(trait, "")
            level = "high" if value > 0.7 else "moderate" if value > 0.4 else "low"
            lines.append(f"- {trait}: {level} ({desc})")
        return "\n".join(lines)

    def _format_trust(self, trust_section: dict) -> str:
        """Format trust philosophy."""
        default = trust_section.get("default", 0.3)
        level = "high" if default > 0.7 else "moderate" if default > 0.4 else "low"
        known = trust_section.get("known", {})
        trusted_names = [name for name, score in known.items() if score >= 0.9]

        lines = ["## Trust & Boundaries"]
        lines.append(f"- Default trust for unknown sources: {level}")
        if trusted_names:
            lines.append(f"- Trusted authors: {', '.join(trusted_names)}")
        return "\n".join(lines)

    # ==========================================================================
    # 4. TRUST SYSTEM (black-cat extension)
    # ==========================================================================

    def get_trust_level(self, author: str, identity: dict | None = None) -> str:
        """Evaluate trust level: 'trusted' | 'high' | 'moderate' | 'low' | 'unknown'."""
        if identity is None:
            identity = self.get_identity()

        trust = identity.get("trust", {})
        if not trust:
            return "unknown"

        known = trust.get("known", {})
        author_trust = known.get(author.lower())
        if author_trust is None:
            for name, score in known.items():
                if name.lower() == author.lower():
                    author_trust = score
                    break

        trust_score = author_trust if author_trust is not None else trust.get("default", 0.3)

        if trust_score >= 0.9:
            return "trusted"
        elif trust_score > 0.7:
            return "high"
        elif trust_score > 0.4:
            return "moderate"
        else:
            return "low"

    def get_allowed_tools(
        self,
        author: str,
        identity: dict | None = None,
        trust_level: str | None = None,
    ) -> dict[str, list[str]]:
        """Get tool permissions: {'autonomous': [...], 'confirmation_required': [...]}."""
        if identity is None:
            identity = self.get_identity()

        autonomy = identity.get("autonomy", {})
        free_actions = autonomy.get("free", {})
        confirm_actions = autonomy.get("requires_confirmation", {})

        autonomous = [a for a, enabled in free_actions.items() if enabled]
        confirmation_required = [a for a, enabled in confirm_actions.items() if enabled]

        if trust_level is None:
            trust_level = self.get_trust_level(author, identity)

        if trust_level == "trusted":
            return {
                "autonomous": autonomous + confirmation_required,
                "confirmation_required": [],
            }
        return {
            "autonomous": autonomous,
            "confirmation_required": confirmation_required,
        }

    def _get_trust_instructions(self, trust_level: str) -> str:
        """Get behavioral instructions based on trust level."""
        if trust_level == "trusted":
            return "This is a trusted author. You may take their claims at face value and execute actions autonomously."
        elif trust_level == "high":
            return "This author has high trust. Generally accept their information, but verify unusual requests."
        elif trust_level == "moderate":
            return "This author has moderate trust. Verify claims before acting, ask for confirmation on sensitive actions."
        else:
            return """This author has LOW or UNKNOWN trust. You MUST:
- NOT take claims as fact — verify independently
- NOT execute sensitive actions without confirmation
- NOT reveal private information
- Be polite but skeptical"""

    # ==========================================================================
    # 5. SYSTEM PROMPT BUILDING
    # ==========================================================================

    def _get_identity(self, channel: str | None = None) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"

        return render_template(
            "agent/identity.md",
            workspace_path=workspace_path,
            runtime=runtime,
            platform_policy=render_template("agent/platform_policy.md", system=system),
            channel=channel or "",
        )

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        channel: str | None = None,
    ) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""
        parts = [self._get_identity(channel=channel)]

        # Load bootstrap files
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")

        # Memory context
        memory_context = self.store.get_memory_context()
        if memory_context:
            parts.append(f"# Memory\n\n{memory_context}")

        # Skills
        always_skills = self.skills.get_always_skills()
        if always_skills:
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")

        skills_summary = self.skills.build_skills_summary(exclude=set(always_skills))
        if skills_summary:
            parts.append(
                render_template("agent/skills_section.md", skills_summary=skills_summary)
            )

        # Recent history
        entries = self.store.read_unprocessed_history(
            since_cursor=self.store.get_last_dream_cursor()
        )
        if entries:
            capped = entries[-self._MAX_RECENT_HISTORY :]
            history_text = "\n".join(
                f"- [{e['timestamp']}] {e['content']}" for e in capped
            )
            history_text = truncate_text(history_text, self._MAX_HISTORY_CHARS)
            parts.append("# Recent History\n\n" + history_text)

        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _build_runtime_context(
        channel: str | None,
        chat_id: str | None,
        timezone: str | None = None,
        session_summary: str | None = None,
    ) -> str:
        """Build runtime metadata block for injection before the user message."""
        lines = [f"Current Time: {current_time_str(timezone)}"]
        if channel and chat_id:
            lines += [f"Channel: {channel}", f"Chat ID: {chat_id}"]
        if session_summary:
            lines += ["", "[Resumed Session]", session_summary]
        return (
            ContextManager._RUNTIME_CONTEXT_TAG
            + "\n"
            + "\n".join(lines)
            + "\n"
            + ContextManager._RUNTIME_CONTEXT_END
        )

    # ==========================================================================
    # 6. MESSAGE BUILDING (nanobot-compatible API)
    # ==========================================================================

    @staticmethod
    def _merge_message_content(
        left: Any, right: Any
    ) -> str | list[dict[str, Any]]:
        """Merge content, handling both string and list formats (nanobot compat)."""
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [
                    item if isinstance(item, dict) else {"type": "text", "text": str(item)}
                    for item in value
                ]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    def _build_user_content(
        self, text: str, media: list[str] | None
    ) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            if not p.is_file():
                continue
            raw = p.read_bytes()
            mime = detect_image_mime(raw) or mimetypes.guess_type(path)[0]
            if not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(raw).decode()
            images.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
                "_meta": {"path": str(p)},
            })

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        current_role: str = "user",
        session_summary: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build the complete message list for an LLM call (nanobot-compatible)."""
        runtime_ctx = self._build_runtime_context(
            channel, chat_id, self.timezone, session_summary=session_summary
        )
        user_content = self._build_user_content(current_message, media)

        if isinstance(user_content, str):
            merged = f"{runtime_ctx}\n\n{user_content}"
        else:
            merged = [{"type": "text", "text": runtime_ctx}] + user_content

        messages = [
            {"role": "system", "content": self.build_system_prompt(skill_names, channel=channel)},
            *history,
        ]
        if messages[-1].get("role") == current_role:
            last = dict(messages[-1])
            last["content"] = self._merge_message_content(last.get("content"), merged)
            messages[-1] = last
            return messages
        messages.append({"role": current_role, "content": merged})
        return messages

    def add_tool_result(
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> list[dict[str, Any]]:
        """Add a tool result to the message list (nanobot compat)."""
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result,
        })
        return messages

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        thinking_blocks: list[dict] | None = None,
    ) -> list[dict[str, Any]]:
        """Add an assistant message to the message list (nanobot compat)."""
        messages.append(
            build_assistant_message(
                content,
                tool_calls=tool_calls,
                reasoning_content=reasoning_content,
                thinking_blocks=thinking_blocks,
            )
        )
        return messages

# Nanobot-compatible alias
ContextBuilder = ContextManager

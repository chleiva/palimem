"""The trust boundary for LLM agents (T-F2): host tier, agent tool tier, audit log and answer rendering.

``Host`` (trusted code only) binds identity; ``Host.bind_session`` returns the ``AgentTools`` an LLM may call.
See docs/API_TRUST_BOUNDARY.md and docs/AGENT_GUIDE.md.
"""

from palimem.agent.audit import AuditLog, AuditRow
from palimem.agent.host import (
    DEFAULT_POLICY_LABEL,
    MAX_EXPLAIN_DEPTH,
    ConnectorEvent,
    ConnectorSpec,
    ExtractorRequired,
    Host,
    HostAppend,
    HostError,
    IngestResult,
    InvalidAuthorityRule,
    KeyRef,
    Notice,
    ReportRow,
    SessionContext,
)
from palimem.agent.render import (
    answer_json,
    answer_text,
    explanation_json,
    explanation_text,
)
from palimem.agent.tools import AgentTools, ToolError, ToolOutput, ToolSpec

__all__ = [
    "DEFAULT_POLICY_LABEL",
    "MAX_EXPLAIN_DEPTH",
    "AgentTools",
    "AuditLog",
    "AuditRow",
    "ConnectorEvent",
    "ConnectorSpec",
    "ExtractorRequired",
    "Host",
    "HostAppend",
    "HostError",
    "IngestResult",
    "InvalidAuthorityRule",
    "KeyRef",
    "Notice",
    "ReportRow",
    "SessionContext",
    "ToolError",
    "ToolOutput",
    "ToolSpec",
    "answer_json",
    "answer_text",
    "explanation_json",
    "explanation_text",
]

"""The document knowledge base and the MCP bridge.

Lifted out of ``create_app``. The handler bodies are unchanged; what was
an implicit closure over the application's service objects is now a
signature that names them.
"""

from __future__ import annotations

import json
import re

from fastapi import Depends, FastAPI, HTTPException
from ..api_models import (
    KnowledgeDocumentUploadRequest,
    McpCallRequest,
)
from ..infrastructure import DuckDBAuditRepository
from ..services import (
    AuditService,
    Principal,
)
from ..services.knowledge_service import KnowledgeService
from ..tools.registry import KnowledgeSearchInput
from typing import Any


def register(
    app: FastAPI,
    *,
    active_config: Any,
    execute_tool: Any,
    researcher: Any,
    tool_registry: Any,
    viewer: Any,
) -> None:
    @app.get("/knowledge/documents")
    def knowledge_documents(
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return execute_tool("list_knowledge_documents", {})
    @app.post("/knowledge/search")
    def knowledge_search(
        request: KnowledgeSearchInput,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        return execute_tool(
            "search_knowledge",
            request.model_dump(mode="json"),
        )
    @app.post("/knowledge/documents")
    def upload_knowledge_document(
        request: KnowledgeDocumentUploadRequest,
        principal: Principal = Depends(researcher),
    ) -> dict[str, Any]:
        """Index a user-supplied document (company report, filing, notes)."""
        text = request.text
        document_type = request.document_type
        if request.content_base64:
            try:
                import pypdf  # noqa: F401
            except ImportError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "PDF support requires the optional pypdf package "
                        "(pip install pypdf). Paste the text or upload a "
                        ".txt/.md file instead."
                    ),
                ) from exc
            import base64
            import io
            try:
                reader = pypdf.PdfReader(
                    io.BytesIO(base64.b64decode(request.content_base64))
                )
                text = "\n\n".join(
                    page.extract_text() or "" for page in reader.pages
                )
                document_type = "pdf"
            except Exception as exc:
                raise HTTPException(
                    status_code=422,
                    detail=f"Could not read the PDF: {exc}",
                ) from exc
        if not text.strip():
            raise HTTPException(
                status_code=400,
                detail=(
                    "The document has no extractable text. Provide 'text' "
                    "or a readable PDF."
                ),
            )
        slug = re.sub(r"[^a-z0-9]+", "-", request.title.lower()).strip("-")
        knowledge = KnowledgeService(active_config.database_path)
        try:
            document = knowledge.index_text(
                title=request.title,
                source_uri=request.source_uri or f"upload://{slug}",
                text=text,
                document_type=document_type,
                metadata={
                    "corpus": "user_uploaded",
                    "uploaded_by": principal.username,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        audit = AuditService(
            DuckDBAuditRepository(active_config.database_path)
        )
        event = audit.record(
            actor=principal.username,
            action="knowledge_document_uploaded",
            entity_type="knowledge_document",
            entity_id=document["document_id"],
            payload={
                "title": request.title,
                "document_type": document_type,
                "chunk_count": document.get("chunk_count"),
            },
        )
        return {**document, "audit_id": event.audit_id}
    @app.get("/mcp/tools")
    def mcp_tools(principal: Principal = Depends(viewer)) -> dict[str, Any]:
        """List platform tools as MCP-compatible tool definitions."""
        allowed = tool_registry.allowed_for_role(principal.role)
        return {
            "protocol": "mcp-compatible",
            "server_name": "iimc-trading-platform",
            "tools": [
                {
                    "name": tool["name"],
                    "description": (
                        f"{tool['description']} "
                        f"Side effects: {tool['side_effects']}."
                    ),
                    "inputSchema": tool["input_schema"],
                }
                for tool in tool_registry.list_tools()
                if tool["name"] in allowed
            ],
        }
    @app.post("/mcp/call")
    def mcp_call(
        request: McpCallRequest,
        principal: Principal = Depends(viewer),
    ) -> dict[str, Any]:
        """Execute a governed platform tool through an MCP-style envelope.

        Uses the same audited execution path as chat: role checks, live
        trading gates, and approval requirements are all preserved.
        """
        allowed = tool_registry.allowed_for_role(principal.role)
        if request.name not in allowed:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Tool {request.name!r} is not available for role "
                    f"{principal.role!r}."
                ),
            )
        try:
            result = execute_tool(request.name, request.arguments)
        except HTTPException as exc:
            detail = exc.detail
            message = (
                detail.get("message", str(detail))
                if isinstance(detail, dict)
                else str(detail)
            )
            return {
                "content": [{"type": "text", "text": message}],
                "isError": True,
            }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, default=str, indent=2),
                }
            ],
            "isError": False,
            "structuredContent": result,
        }

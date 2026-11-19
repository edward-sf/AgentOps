from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError


class NotFoundError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class ConflictError(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


_UNIQUE_MESSAGES = {
    "uq_agents_name": "Agent name already exists",
    "uq_agent_versions_agent_id_version": "Agent version already exists",
    "uq_datasets_name": "Dataset name already exists",
    "uq_test_cases_dataset_id_key": "Test case key already exists in this dataset",
}

# Referenced-table name -> entity shown in the message.
_REFERENCED_ENTITIES = {
    "agents": "Agent",
    "agent_versions": "Agent version",
    "datasets": "Dataset",
    "test_cases": "Test case",
}


def _constraint_name(exc: IntegrityError) -> str | None:
    diag = getattr(exc.orig, "diag", None)
    name = getattr(diag, "constraint_name", None)
    if name is None:
        # asyncpg wraps the driver exception; the original sits one level down.
        cause = getattr(exc.orig, "__cause__", None)
        name = getattr(cause, "constraint_name", None)
    return name if isinstance(name, str) else None


def integrity_conflict(exc: IntegrityError, entity: str | None = None) -> ConflictError:
    """Translate a database IntegrityError into a 409 with a stable message.

    `entity` names what was being deleted, used when a foreign key blocks the delete.
    """
    name = _constraint_name(exc)
    if name in _UNIQUE_MESSAGES:
        return ConflictError(_UNIQUE_MESSAGES[name])
    if name is not None and name.startswith("fk_"):
        return ConflictError(f"{entity or 'Record'} is referenced by evaluation history")
    return ConflictError("Conflict with existing data")


def register_error_handlers(app: FastAPI) -> None:
    async def not_found(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, NotFoundError)
        return JSONResponse({"detail": exc.detail}, status_code=404)

    async def conflict(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ConflictError)
        return JSONResponse({"detail": exc.detail}, status_code=409)

    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(ConflictError, conflict)

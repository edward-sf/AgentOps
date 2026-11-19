"""A scripted stand-in for a model provider. Runtime tests never call a model API."""

from app.agents.providers.base import ModelRequest, ModelResponse, ToolCall


class ScriptedProvider:
    """Answers the n-th request with the n-th script entry; an exception entry is raised."""

    name = "anthropic"

    def __init__(self, script: list[ModelResponse | Exception]) -> None:
        self._script = list(script)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._script:
            raise AssertionError("script exhausted")
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def response(
    stop_reason: str,
    text: str | None = None,
    tool_calls: tuple[ToolCall, ...] = (),
) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        response_model="claude-sonnet-5-5",
        input_tokens=100,
        output_tokens=20,
    )


def end_turn(text: str = "It is 12°C and raining") -> ModelResponse:
    return response("end_turn", text=text)


def tool_use(
    name: str = "get_weather",
    arguments: dict[str, object] | None = None,
    call_id: str = "toolu_1",
) -> ModelResponse:
    args = {"location": "Seattle"} if arguments is None else arguments
    return response("tool_use", tool_calls=(ToolCall(id=call_id, name=name, arguments=args),))

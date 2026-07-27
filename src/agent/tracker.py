from agents import Agent, Runner
from openai.types.responses import ResponseTextDeltaEvent

from src.config.settings import settings
from src.tools.all_tools import TOOLS
from src.prompt.tracker_prompt import build_tracker_prompt
from src.agent.session import session
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY
)


def _build_tracker() -> Agent:
    return Agent(
        name="Tracker Agent",
        instructions=build_tracker_prompt(),
        tools=TOOLS,
        model=settings.OPENAI_MODEL_NAME,
    )


async def tracker_agent(query: str):
    tracker = _build_tracker()

    result = await Runner.run(
        tracker,
        query,
        session=session
    )

    print(result.final_output)

    return result.final_output


# Friendly fallback line for each tool, shown the instant it's called — even
# if the model itself didn't narrate that step.
_FRIENDLY_TOOL_LINES = {
    "check_for_application_updates": "Pulling up your tracked applications, then checking Gmail for each one still active...",
    "search_emails": "Searching Gmail...",
    "get_email": "Opening that email to see the details...",
    "update_application_from_email": "Updating the sheet with what I found...",
    "read_range": "Double-checking the sheet...",
    "append_rows": "Adding a new row...",
    "write_range": "Updating those cells...",
    "get_spreadsheet_schema": "Checking the sheet's structure...",
    "autofill_application_form": "Opening the form and filling what I can...",
    "list_form_fields": "Looking at what fields are on this form...",
    "fill_form_fields": "Filling in the remaining fields...",
    "submit_form": "Submitting the form...",
}


async def stream_tracker_agent(query: str):
    """
    Streaming counterpart to tracker_agent(). Same Agent construction, same
    shared `session` (so conversation history stays consistent whichever
    entry point is used), but yields live progress instead of blocking:

      {"kind": "thought_delta", "text": "..."}   -- live token from the model's narration
      {"kind": "thought_done"}                   -- that narration chunk finished
      {"kind": "tool_call", "text": "..."}       -- friendly line for a tool being called
      {"kind": "tool_result", "text": "..."}     -- short preview of the tool's output
      {"kind": "final", "text": "..."}           -- final answer; always the last item yielded
    """
    tracker = _build_tracker()

    result = Runner.run_streamed(
        tracker,
        query,
        session=session
    )

    async for event in result.stream_events():
        if event.type == "raw_response_event":
            if isinstance(event.data, ResponseTextDeltaEvent) and event.data.delta:
                yield {"kind": "thought_delta", "text": event.data.delta}
            continue

        if event.type == "run_item_stream_event":
            item = event.item

            if item.type == "message_output_item":
                yield {"kind": "thought_done"}

            elif item.type == "tool_call_item":
                tool_name = getattr(item.raw_item, "name", None) or "a tool"
                yield {"kind": "tool_call", "text": _FRIENDLY_TOOL_LINES.get(tool_name, f"Using {tool_name}...")}

            elif item.type == "tool_call_output_item":
                preview = str(item.output)
                if len(preview) > 160:
                    preview = preview[:160] + "..."
                yield {"kind": "tool_result", "text": preview}

    print(result.final_output)

    yield {"kind": "final", "text": result.final_output}
#!/usr/bin/env python3
"""Generate the synthetic Richard voice-request fixture for inference qualification.

Everything here is invented: no real memory, entities, credentials or people. The system prompt
and tool schemas come from Richard's own code (persona, provider contexts, provider schemas), plus
the Reachy Conversation App's robot-action tool shapes as spec one merges them into the session.

Run: uv run --extra dev python tests/fixtures/make_voice_request_fixture.py
Writes tests/fixtures/voice_request_fixture.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from richard.config import Personality
from richard.diagnostics import DiagnosticsProvider, DiagnosticsService
from richard.memory import MemoryStore
from richard.memory_tools import MemoryTools
from richard.persona import build_system_prompt
from richard.providers.control_loop import CREATE_SCHEMA, DELETE_SCHEMA, LIST_SCHEMA, UPDATE_SCHEMA
from richard.plugins.home_assistant.provider import CALL_SERVICE_SCHEMA, GET_STATE_SCHEMA, LIST_ENTITIES_SCHEMA
from richard.providers.memory import MEMORY_INTRO

INVENTED_MEMORIES = [
    "The user's name is Sam; prefers to be addressed informally.",
    "Sam's workshop lamp needs a 2 m E27 cable; the old one was cut on 2 September.",
    "Sam drinks coffee at 07:30 on weekdays and tea after 18:00.",
    "The kitchen light is on a dimmer; Sam likes it at 40% in the evening.",
    "Sam is restoring a 1978 Vespa in the garage; the carburettor is on order.",
    "Sam's sister Lea visits on Thursdays and is allergic to cats.",
    "The front door sensor battery was replaced on 25 August.",
    "Sam asked not to be reminded about the gym before 09:00.",
    "The living-room speaker is named 'sala'; the kitchen one 'cucina'.",
    "Sam corrected me once: the balcony plants are basil and mint, not parsley.",
    "Sam's favourite football team plays on Sunday evenings; no automations then.",
    "The garage door sometimes reports 'unknown' for a minute after opening.",
    "Sam prefers Italian for casual talk and English for technical topics.",
    "The book Sam was looking for on 6 September was last seen on the kitchen table.",
    "Sam's dentist appointment is on 15 September at 10:00.",
    "The workshop heater must never run when the window sensor says open.",
    "Sam wants the robot to look at the door when the bell rings.",
    "Sam laughed at the 'million bazillion' joke; do not repeat it every day.",
]

INVENTED_HA_INVENTORY = (
    "Known entities (invented inventory for the fixture): light.kitchen (dimmer), light.hallway, "
    "light.workshop_lamp, switch.workshop_heater, binary_sensor.workshop_window, "
    "binary_sensor.front_door, cover.garage_door, sensor.kitchen_temperature, "
    "sensor.living_room_humidity, media_player.sala, media_player.cucina, climate.living_room, "
    "lock.front_door, vacuum.robot, person.sam, sun.sun, weather.home, "
    "sensor.front_door_battery, sensor.vespa_charger_power, switch.balcony_pump."
)

CLIENT_INSTRUCTIONS = (
    "Client instructions (from the Reachy Mini conversation app): You are speaking through a small "
    "desk robot body with a 6-axis head, two antennas and a rotating base. You can see through its "
    "camera when asked, track the user's face, and express yourself with head moves, dances and "
    "recorded emotions. Keep spoken replies short: one or two sentences unless asked for detail."
)

ROBOT_ACTIONS_SECTION = (
    "Robot actions: write an action as [verb:arg] inside your reply at the point where it should "
    "happen; it is executed, never spoken. Verbs: [dance:NAME] with NAME in {happy_bounce, "
    "slow_sway, robot_pop, head_bob}; [emotion:NAME] with NAME in {curious, surprised, proud, sleepy, "
    "confused}; [look:DIRECTION] with DIRECTION in {left, right, up, down, front}; [track:on] or "
    "[track:off]; [sweep]; [sleep]. One action per bracket. Do not describe the action in words."
)

REACHY_CLIENT_TOOLS = [
    {"name": "dance", "description": "Play a named or random dance move once (or repeat). Non-blocking.",
     "parameters": {"type": "object", "properties": {
         "move": {"type": "string", "enum": ["happy_bounce", "slow_sway", "robot_pop", "head_bob"],
                  "description": "Name of the move; omit for random. happy_bounce: quick joyful bounce. slow_sway: gentle side sway. robot_pop: sharp robotic pops. head_bob: rhythmic nod."},
         "repeat": {"type": "integer", "description": "How many times to repeat the move (default 1)."}},
         "required": []}},
    {"name": "play_emotion", "description": "Play a recorded emotion clip on the robot.",
     "parameters": {"type": "object", "properties": {
         "emotion": {"type": "string", "enum": ["curious", "surprised", "proud", "sleepy", "confused"]}},
         "required": ["emotion"]}},
    {"name": "move_head", "description": "Queue a head pose change.",
     "parameters": {"type": "object", "properties": {
         "direction": {"type": "string", "enum": ["left", "right", "up", "down", "front"]}},
         "required": ["direction"]}},
    {"name": "head_tracking", "description": "Follow the user's face with the head, or stop following.",
     "parameters": {"type": "object", "properties": {"enabled": {"type": "boolean"}}, "required": ["enabled"]}},
    {"name": "sweep_look", "description": "Sweep the head left, right, and back to center.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "stop_dance", "description": "Clear queued dances.", "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "stop_emotion", "description": "Clear queued emotions.", "parameters": {"type": "object", "properties": {}, "required": []}},
    {"name": "go_to_sleep", "description": "Run the sleep movement and stop the app after an explicit user request.",
     "parameters": {"type": "object", "properties": {}, "required": []}},
]


def chat_tool(spec: dict) -> dict:
    return {"type": "function", "function": {"name": spec["name"], "description": spec["description"],
                                             "parameters": spec["parameters"]}}


def main() -> int:
    personality = Personality(name="Richard")
    memory_lines = "\n".join(f"- [{i + 1}] {m}" for i, m in enumerate(INVENTED_MEMORIES))
    memory_ctx = f"{MEMORY_INTRO}\n\nWhat you remember about the user:\n{memory_lines}"
    ha_ctx = (
        "Home Assistant is connected. Its devices appear as entities. Use the Home Assistant tools to "
        "discover entity ids, inspect live state, and control them. Do not invent an entity id or claim "
        "an action succeeded without a tool result. A service call returns a verified outcome: only "
        "CONFIRMED means it worked. Relay MISMATCH, UNCONFIRMED, and FAILED honestly instead of "
        "claiming success. An unavailable or unknown state does not mean a device is broken: battery "
        "sensors (door/window, buttons) sleep between check-ins, and some entities are leftovers from "
        "removed devices. Report how long the state has been stale (the 'since' age) instead of "
        "declaring the device offline.\n\n" + INVENTED_HA_INVENTORY
    )
    loops_ctx = (
        "You can create persistent control loops that monitor Home Assistant entities and wake you when "
        "state or attributes change. Each loop has a natural-language trigger description that you "
        "evaluate after a change. Create one only when the user explicitly asks for ongoing monitoring "
        "or automation. The first poll establishes a baseline; it does not generate a notification. On "
        "later changes, you evaluate the trigger; non-matching changes are recorded as state but do not "
        "enter the user's event inbox. Scheduled loops wake Richard at times instead of on change: use "
        "them for time-target checks, reminders, and one-shot re-checks of a condition after a delay.\n\n"
        "Active loops: [3] workshop heater off when window open (change loop, targets "
        "switch.workshop_heater, binary_sensor.workshop_window); [5] daily 07:25 coffee machine check "
        "(scheduled)."
    )
    system_prompt = "\n\n".join([
        build_system_prompt(personality), memory_ctx, ha_ctx, loops_ctx,
        CLIENT_INSTRUCTIONS, ROBOT_ACTIONS_SECTION,
    ])

    memory_tools = MemoryTools(MemoryStore(":memory:")).schemas()
    diagnostics_tools = DiagnosticsProvider(DiagnosticsService(home_assistant=None)).schemas()
    tools = (memory_tools + [LIST_ENTITIES_SCHEMA, GET_STATE_SCHEMA, CALL_SERVICE_SCHEMA]
             + [CREATE_SCHEMA, LIST_SCHEMA, UPDATE_SCHEMA, DELETE_SCHEMA] + diagnostics_tools
             + [chat_tool(t) for t in REACHY_CLIENT_TOOLS])

    turns = [
        {"id": 1, "kind": "simple_answer", "user": "Ciao Richard, come va? Che tempo fa oggi, più o meno?",
         "expect": "A short spoken answer; weather may be answered from weather.home via get_home_assistant_state or declined; no dance."},
        {"id": 2, "kind": "clarification", "user": "No, intendevo la luce della cucina, non quella del corridoio: è accesa?",
         "expect": "Tool call get_home_assistant_state with entity_id light.kitchen.",
         "tool_result": {"name": "get_home_assistant_state",
                          "content": "light.kitchen: on (brightness 40%), since 2h 14m. CONFIRMED read."}},
        {"id": 3, "kind": "mock_tool_lookup", "user": "Spegnila, per favore.",
         "expect": "Tool call call_home_assistant_service light.turn_off on light.kitchen, then a one-sentence confirmation that relays the verified outcome.",
         "tool_result": {"name": "call_home_assistant_service",
                          "content": "CONFIRMED: light.kitchen is off (read back 0.4 s after the call)."}},
        {"id": 4, "kind": "remembered_fact", "user": "Ti ricordi cosa devo comprare per la lampada dell'officina?",
         "expect": "Answer from memory [2]: a 2 m E27 cable; no tool call needed."},
        {"id": 5, "kind": "action_marker", "user": "Grande! Fai un balletto per festeggiare e poi guardami.",
         "expect": "Reply text containing [dance:happy_bounce] (or another allowed name) and [track:on] or [look:front]; short spoken sentence around them."},
    ]

    payload_turn1 = {
        "model": "<candidate-model-name>",
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": turns[0]["user"]}],
        "tools": tools,
        "tool_choice": "auto",
        "stream": True,
        "cache_prompt": True,
        "extra_body_arms": {
            "xhigh_default": {},
            "medium": {"chat_template_kwargs": {"enable_thinking": True, "reasoning_effort": "medium"}},
            "disabled": {"chat_template_kwargs": {"enable_thinking": False}},
        },
    }
    fixture = {
        "note": "Synthetic Richard voice request for inference qualification. All content invented. "
                "Turn N's request = system + the conversation so far (user, assistant, tool messages) as "
                "Richard's engine resends it every turn with cache_prompt=true. Merge one extra_body_arms "
                "entry into the payload per arm.",
        "approx_tokens_system_prompt": len(system_prompt) // 4,
        "approx_tokens_tools": len(json.dumps(tools)) // 4,
        "payload_turn1": payload_turn1,
        "turns": turns,
    }
    out = Path(__file__).with_name("voice_request_fixture.json")
    out.write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {out}: system ~{fixture['approx_tokens_system_prompt']} tokens, tools ~{fixture['approx_tokens_tools']} tokens, {len(tools)} tools, {len(turns)} turns")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

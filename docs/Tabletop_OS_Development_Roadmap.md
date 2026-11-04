# Tabletop OS Development Roadmap

> Software update (2026-10-16): The single-piece PC runtime, two-pinch servo-lift sequence, simulation, offline vision integration and PC-to-Pi final-frame path are implemented. Physical commissioning remains pending. See [the current technical guide](TABLETOP_OS_TECHNICAL_GUIDE_ZH.md) for actual code and verification; the phases below remain a roadmap, not a current acceptance record.

> Architecture note (2026-10-12): Pi-side cursor and trail composition below describes the retained v0.1 diagnostic baseline. The production direction is for the PC runtime to compose the final display frame and for the Pi to act as the HUB75 hardware output and safety bridge.

> Direction: evolve the earlier translucent board game assistance system into a physical-digital tabletop where players, game logic, and an LLM can all become valid inputs, and the board can respond through light plus physical motion.

## 1. Updated Project Positioning

Tabletop OS is no longer only a board game assistant. The new version should be framed as a negotiation system between three actors:

- Player: moves physical pieces, gestures, speaks, or types intent.
- Game engine: maintains rules, state, constraints, and win conditions.
- LLM layer: interprets natural-language intent and proposes rule/content changes.

The physical table becomes the shared interface. It reads player action, displays game feedback, and can move pieces through an electromagnetic CoreXY mechanism.

## 2. Hardware Direction

### Recommended MVP Mechanism

Use a CoreXY gantry under the board, carrying one controllable electromagnet.

This is more practical than building a full electromagnetic coil matrix at the first stage:

- lower cost and wiring complexity;
- easier calibration and debugging;
- stronger and more predictable magnetic force;
- reusable motion-control patterns from 3D printers/CNC systems;
- supports a convincing demo: one piece can be automatically moved to target cells.

### Physical Stack

- Top layer: translucent/acrylic board surface with printed or projected grid.
- Game piece: embedded magnet, optionally with fiducial marker or color marker.
- Under-table motion layer: CoreXY carriage with electromagnet.
- Sensing layer: camera above or below the table for piece position detection.
- Feedback layer: LED matrix/light board or projection for hints, range, attack, health, and generated events.

### Control Hardware

- MCU: ESP32, Arduino-compatible controller, or GRBL-compatible motion board.
- Motion: two stepper motors for CoreXY movement.
- Magnet: MOSFET-controlled electromagnet with current limiting and heat safety.
- Host: laptop running the dedicated Tabletop OS game-authoring runtime, vision/sensing, rule logic, LLM bridge, and UI. Supporting general-purpose engines such as Unity or Godot is not a current goal.

## 3. System Architecture

```text
Physical Board
  -> camera / sensors
  -> state tracker
  -> game engine
  -> rule engine / LLM input layer
  -> motion planner
  -> CoreXY + electromagnet
  -> physical piece movement
  -> player response
```

The important design choice is to keep the LLM outside the low-level control loop. The LLM should propose high-level intentions, while the deterministic rule engine validates whether those intentions are legal and safe.

## 4. LLM As Input Entrance

The LLM should not directly control motors. It should translate natural language into structured commands.

Example player input:

```text
"Make this round more defensive."
"Create a new obstacle between these two pieces."
"Move the enemy one step closer, but do not attack yet."
```

Example structured output:

```json
{
  "type": "rule_proposal",
  "intent": "increase_defense",
  "changes": [
    {
      "target": "all_players",
      "attribute": "shield",
      "delta": 1,
      "duration_turns": 2
    }
  ],
  "requires_confirmation": true
}
```

The game engine then decides:

- Is this allowed by the current game mode?
- Does it preserve fairness?
- Does it require player confirmation?
- Can it be represented physically through lights or movement?

## 5. Protocol Draft

Use a small message protocol between modules. This keeps hardware, game logic, and AI replaceable.

### Board State

```json
{
  "type": "board_state",
  "timestamp": 0,
  "pieces": [
    {
      "id": "p1",
      "team": "player",
      "cell": { "x": 2, "y": 3 },
      "pose": 0,
      "status": ["selected"]
    }
  ]
}
```

### LLM Command

```json
{
  "type": "llm_command",
  "source": "voice_or_text",
  "text": "summon a wall near my piece"
}
```

### Validated Game Action

```json
{
  "type": "game_action",
  "action": "spawn_obstacle",
  "cell": { "x": 3, "y": 3 },
  "visual": "warning_pulse",
  "physical_motion": null
}
```

### Motion Command

```json
{
  "type": "motion_command",
  "piece_id": "enemy_1",
  "from": { "x": 5, "y": 2 },
  "to": { "x": 4, "y": 2 },
  "speed": "slow",
  "magnet": true
}
```

## 6. MVP Phases

### Current Integration Demonstration

Before building the full authoring environment, complete one end-to-end interaction with a single known magnetic piece:

1. Whenever the hand is tracked inside the active table area, the PC runtime draws a circular cursor into the final 64×96 frame and sends it to the Pi for HUB75 output.
2. The user places the hand above the piece and makes a grab gesture to select it; grabbing changes the cursor style but does not move the mechanism.
3. Hand movement continuously updates both the cursor and target preview in the PC-composited frame; the mechanism does not chase the hand.
4. A release or explicit confirmation gesture commits one numbered `MOVE_TO` task.
5. The Pi forwards the task to the Arduino and receives state telemetry during motion.
6. The Pi returns reported actuator positions to the PC. The PC renders a blue-only motion trail into the final frame, while the Pi displays the pixels and keeps the faulty red output disabled. Old trail points fade out after a configurable lifetime.

This demonstration uses the dedicated Tabletop OS software path. It does not require a generic game engine, a plugin ecosystem, multiple pieces, or an LLM.

### Phase 1: Digital Simulation

Goal: prove the interaction logic before hardware complexity.

- Build a grid-based board state model.
- Add draggable pieces in an early version of the dedicated Tabletop OS authoring/runtime application.
- Add a deterministic rule engine.
- Add text input for LLM-style commands, initially parsed with fixed rules.
- Output validated actions as JSON.

Success condition: player input, rule validation, and generated board events work without physical hardware.

### Phase 2: Vision-Based Sensing

Goal: connect real pieces to digital state.

- Use camera tracking with colored markers, AprilTags, or ArUco markers.
- Map camera coordinates to board cells.
- Detect piece id and position.
- Update the digital board state in real time.

Success condition: moving one real piece updates its digital cell reliably.

### Phase 3: CoreXY Electromagnetic Movement

Goal: create physical output.

- Build CoreXY gantry under the board.
- Mount electromagnet on the carriage.
- Calibrate board cells to gantry coordinates.
- Implement move-to-cell and piece-drag motion.
- Add emergency stop and magnet timeout.

Success condition: the system can move one magnetic piece from cell A to cell B.

### Phase 4: LLM Input Layer

Goal: make natural language a real entrance into the game system.

- Add text or voice input.
- Ask the LLM to return structured JSON only.
- Validate all actions through the rule engine.
- Show proposed changes visually before applying them.
- Let the player confirm or reject major rule changes.

Success condition: a player can ask for a rule/content change, and the system turns it into a visible, validated board event.

### Phase 5: Integrated Demo Game

Goal: produce a portfolio-ready experience.

Recommended demo format:

- 5x5 or 6x6 board.
- One player piece, one enemy piece, several obstacles.
- Player moves physically.
- System senses the move.
- LLM introduces events or modifiers.
- Board displays range, warning, health, and generated events.
- CoreXY electromagnet moves one piece as system feedback.

## 7. Design Rules

- The LLM proposes; the rule engine decides.
- Tabletop OS uses its own game-authoring and runtime environment; general-purpose game-engine compatibility is not part of the current MVP.
- Physical motion should be slow, legible, and intentional.
- Every AI-generated change should have a visible tabletop representation.
- Keep the first demo turn-based rather than real-time.
- Prefer one reliable moving piece over many unreliable moving pieces.
- Use light feedback for complex state, and physical movement for meaningful state changes.

## 8. Portfolio Narrative

The strongest story is the shift from "board game assistance" to "AI-mediated tangible play."

Suggested framing:

> Tabletop OS explores how physical game pieces can become a shared negotiation surface between human intention, computational rules, and generative AI. Instead of letting AI replace play, the project asks how AI can enter the game through a tangible interface that players can still touch, contest, and control.

## 9. Immediate Next Steps

1. Revalidate homing, distance, direction, soft limits, and safe speeds with the current 1/16 microstep, 100 steps/mm Arduino configuration.
2. Build the Pi serial bridge for numbered `MOVE_TO` tasks and `STATE` telemetry.
3. Use the retained Pi-side blue trail and cursor as a diagnostic to validate Arduino telemetry, HUB75 mapping, and the faulty red-channel mask.
4. Build the PC final-frame compositor and Pi frame receiver, then create a mouse-driven cursor, selection, target preview, trail, and confirm behavior without continuously driving the mechanism.
5. Replace mouse input with calibrated camera-based hand position plus grab and release/confirm gestures; the live cursor behavior must remain unchanged.
6. Complete the single-piece end-to-end demonstration before adding AprilTag identity, board rules, multiple games, or LLM input.

## 10. Deferred Feature: Occlusion-Resilient Piece State

> Status (2026-10-27): planned for later development. Do not treat this section as implemented behavior or as permission to relax the current motion safety gates.

Goal: preserve a piece's logical state while its marker is temporarily hidden by a hand, glare, or the mechanism, without allowing stale camera data to authorize unsafe physical motion.

### Proposed State Model

- Keep camera measurements, persistent piece tracks, and physical manipulation state as three separate layers.
- Store each track's last observed pose, observation time, confidence/freshness, pose source, and one explicit state: `VISIBLE`, `OCCLUDED`, `ATTACHED`, `UNVERIFIED`, or `LOST`.
- A missed frame changes visibility/freshness; it does not delete the piece or silently refresh the old observation timestamp.
- The UI may retain the last known position indefinitely, but must visibly distinguish live, occluded, predicted, and unverified positions.

### Motion Policy

- Before pickup, retain the selection and last position during occlusion, but keep the carriage released and wait for a bounded, fresh, unique observation within the pickup tolerance before engaging.
- Once actuator engagement is confirmed, make the carriage position plus the calibrated pickup offset authoritative for the attached piece. Camera loss during an attached drag must not by itself cancel the drag.
- After release, retain the commanded target as a predicted position and require several new camera samples to verify or correct it.
- If post-release verification times out, keep the piece as `UNVERIFIED`, show that state to the user, and block further automatic movement of that piece until it is reacquired.
- A contradictory fresh observation, duplicate marker ID, device fault, disconnect, or explicit stop remains a safety event and must never be hidden by track persistence.
- Hand/marker overlap may later be used to classify a missing tag as `OCCLUDED`, but it is an enhancement rather than a prerequisite for the state model.

### Acceptance Criteria

1. Covering a selected marker during approach does not erase the selection; the mechanism waits released and does not act from stale pose data.
2. Covering a marker after confirmed engagement does not interrupt an otherwise healthy drag.
3. Reappearing markers update the same piece identity and correct the predicted position without a visual jump to another ID.
4. A piece physically moved while hidden is not picked up from its obsolete cached position.
5. Release verification survives short occlusion and clearly reports an unresolved `UNVERIFIED` result after its bounded timeout.
6. Automated tests cover missed frames, long occlusion, reacquisition, duplicate IDs, contradictory positions, disconnects, and stop/reset behavior.


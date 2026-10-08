# ADR 0006 - Voice: dedicated wake word + local speech recognition

Status: accepted

Tried first: using the speech recogniser to spot the wake word. On recorded audio Whisper wrote
"Jarvis" as "giardis", "vi" or "già vi" and a medium model needed seconds of CPU per phrase, so it is
neither reliable nor cheap enough to run continuously. A dedicated detector (openWakeWord, ONNX, a few
MB) scored >= 0.99 on "hey jarvis" and < 0.07 on other speech and runs in real time.

Decision: openWakeWord for the wake word; energy-based endpointing to capture the command;
faster-whisper (int8, CPU) only for that short command; everything local. Only recognised text reaches
the assistant. While the assistant answers the microphone is ignored (no echo cancellation yet), and
voice can never approve an action: approvals stay a deliberate click, so audio from a video or a TV
cannot authorise anything.

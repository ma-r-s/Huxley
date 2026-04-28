import { useEffect, useRef } from "react";
import type { TranscriptEntry } from "../types.js";

interface PartialEntry {
  role: "user" | "assistant";
  text: string;
}

interface TranscriptDrawerProps {
  messages: TranscriptEntry[];
  partial: PartialEntry | null;
  expanded: boolean;
  onToggle: () => void;
}

export function TranscriptDrawer({
  messages,
  partial,
  expanded,
  onToggle,
}: TranscriptDrawerProps) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight;
    }
  }, [messages, partial, expanded]);

  return (
    <div className={`hux-transcript${expanded ? " expanded" : ""}`}>
      <button
        className="hux-transcript-handle"
        onClick={onToggle}
        aria-label="Toggle transcript"
      >
        <span className="hux-handle-bar" />
        <span className="hux-handle-label">
          {expanded
            ? "Tap to collapse"
            : `Transcript${messages.length ? " \u00b7 " + messages.length : ""}`}
        </span>
      </button>
      <div className="hux-transcript-scroll" ref={scrollerRef}>
        {messages.length === 0 && !partial && (
          <div className="hux-transcript-empty">No messages yet</div>
        )}
        {messages.map((m) => (
          <div key={m.id} className={`hux-msg hux-msg-${m.role}`}>
            <div className="hux-msg-role">
              {m.role === "user" ? "You" : "Huxley"}
            </div>
            <div className="hux-msg-text">{m.text}</div>
          </div>
        ))}
        {partial && (
          <div className={`hux-msg hux-msg-${partial.role} hux-msg-partial`}>
            <div className="hux-msg-role">
              {partial.role === "user" ? "You" : "Huxley"}
            </div>
            <div className="hux-msg-text">
              {partial.text}
              <span className="hux-caret" />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

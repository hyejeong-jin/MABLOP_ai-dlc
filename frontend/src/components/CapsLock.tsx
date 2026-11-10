// Caps Lock indicator for password fields. Browsers only reveal the state on a key event,
// so the state is "unknown" until the first key press inside a password field.
import { useState, type KeyboardEvent } from "react";
import { Keyboard, TriangleAlert } from "lucide-react";

export type CapsState = "unknown" | "on" | "off";

export function useCapsLock() {
  const [caps, setCaps] = useState<CapsState>("unknown");

  function sync(e: KeyboardEvent<HTMLElement>) {
    if (typeof e.getModifierState === "function") {
      setCaps(e.getModifierState("CapsLock") ? "on" : "off");
    }
  }

  return { caps, bind: { onKeyDown: sync, onKeyUp: sync } };
}

export function CapsLockHint({ state }: { state: CapsState }) {
  if (state === "on") {
    return (
      <p className="caps-hint caps-on" aria-live="polite">
        <TriangleAlert size={14} aria-hidden="true" />
        Caps Lock 켜짐 · 대문자로 입력되고 있어요
      </p>
    );
  }
  return (
    <p className="caps-hint" aria-live="polite">
      <Keyboard size={14} aria-hidden="true" />
      {state === "off" ? "Caps Lock 꺼짐" : "Caps Lock 상태는 입력을 시작하면 표시돼요"}
    </p>
  );
}
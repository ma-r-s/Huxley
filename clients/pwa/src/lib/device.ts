/**
 * One-shot device snapshot sent to the server log on every connection
 * (`client.device`). Layout bugs on real phones (white band under the
 * status bar, transcript bar off-screen) depend on the iOS version, the
 * standalone/browser mode and how each viewport unit resolves; without
 * this the server log can't tell an iOS 18 tab from an iOS 26 home-screen
 * app. `shell` and `audio` say which client this is: the native iOS app,
 * the home-screen web app or a browser tab, and whether audio runs through
 * the native plugin or WebAudio (an old app install falls back to web).
 */

import { Capacitor } from "@capacitor/core";
import { isNativeAudioAvailable } from "./audio/native.js";

function probePx(css: string): number {
  const el = document.createElement("div");
  el.style.cssText = `position:fixed;visibility:hidden;pointer-events:none;${css}`;
  document.body.appendChild(el);
  const rect = el.getBoundingClientRect();
  el.remove();
  return Math.round(rect.height);
}

export function deviceSnapshot(): Record<string, unknown> {
  const nav = navigator as Navigator & { standalone?: boolean };
  const vv = window.visualViewport;
  const standalone =
    nav.standalone === true ||
    window.matchMedia("(display-mode: standalone)").matches;
  return {
    shell: Capacitor.isNativePlatform()
      ? "native_app"
      : standalone
        ? "home_screen"
        : "browser",
    audio: isNativeAudioAvailable() ? "native" : "web",
    ua: navigator.userAgent,
    standalone,
    inner: `${window.innerWidth}x${window.innerHeight}`,
    visual: vv ? `${Math.round(vv.width)}x${Math.round(vv.height)}` : null,
    screen: `${window.screen.width}x${window.screen.height}`,
    dpr: window.devicePixelRatio,
    safe_top: probePx("height:env(safe-area-inset-top)"),
    safe_bottom: probePx("height:env(safe-area-inset-bottom)"),
    lvh: probePx("height:100lvh"),
    dvh: probePx("height:100dvh"),
    svh: probePx("height:100svh"),
    visibility: document.visibilityState,
  };
}

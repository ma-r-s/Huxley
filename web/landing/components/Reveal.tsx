"use client";
// Scroll-reveal primitives: <Reveal>, <Stagger>, <RevealWords>.
// Once an element scrolls past `threshold` visibility, it stays revealed —
// re-triggering on scroll-back is more distracting than meaningful for a
// landing page. Ported from the prototype's voice-thread.jsx.

import type { CSSProperties, ElementType, ReactNode } from "react";
import { useInView } from "../lib/voiceThread";

interface RevealProps {
  children: ReactNode;
  delay?: number;
  y?: number;
  duration?: number;
  as?: ElementType;
  style?: CSSProperties;
  id?: string;
}

export function Reveal({
  children,
  delay = 0,
  y = 24,
  duration = 700,
  as: Tag = "div",
  style,
  ...rest
}: RevealProps) {
  const [ref, seen] = useInView<HTMLElement>(0.12);
  return (
    <Tag
      ref={ref}
      {...rest}
      style={{
        ...style,
        opacity: seen ? 1 : 0,
        transform: seen ? "translateY(0)" : `translateY(${y}px)`,
        transition: `opacity ${duration}ms cubic-bezier(.2,.7,.2,1) ${delay}ms, transform ${duration}ms cubic-bezier(.2,.7,.2,1) ${delay}ms`,
        willChange: "opacity, transform",
      }}
    >
      {children}
    </Tag>
  );
}

interface StaggerProps<T> {
  items: T[];
  step?: number;
  initialDelay?: number;
  y?: number;
  duration?: number;
  children: (item: T, index: number) => ReactNode;
}

export function Stagger<T>({
  items,
  step = 70,
  initialDelay = 0,
  y = 24,
  duration = 650,
  children,
}: StaggerProps<T>) {
  return (
    <>
      {items.map((item, i) => (
        <Reveal
          key={i}
          delay={initialDelay + i * step}
          y={y}
          duration={duration}
        >
          {children(item, i)}
        </Reveal>
      ))}
    </>
  );
}

interface RevealWordsProps {
  text: string;
  step?: number;
  initialDelay?: number;
  y?: number;
  duration?: number;
  as?: ElementType;
  style?: CSSProperties;
}

export function RevealWords({
  text,
  step = 40,
  initialDelay = 0,
  y = 18,
  duration = 600,
  as: Tag = "span",
  style,
  ...rest
}: RevealWordsProps) {
  const [ref, seen] = useInView<HTMLElement>(0.2);
  const words = String(text).split(/(\s+)/); // preserve whitespace tokens
  let wordIndex = -1;
  return (
    <Tag ref={ref} {...rest} style={{ ...style, display: "inline-block" }}>
      {words.map((w, i) => {
        if (/^\s+$/.test(w)) return <span key={i}>{w}</span>;
        wordIndex += 1;
        const delay = initialDelay + wordIndex * step;
        return (
          <span
            key={i}
            style={{
              display: "inline-block",
              overflow: "hidden",
              verticalAlign: "baseline",
            }}
          >
            <span
              style={{
                display: "inline-block",
                opacity: seen ? 1 : 0,
                transform: seen ? "translateY(0)" : `translateY(${y}px)`,
                transition: `opacity ${duration}ms cubic-bezier(.2,.7,.2,1) ${delay}ms, transform ${duration}ms cubic-bezier(.2,.7,.2,1) ${delay}ms`,
                willChange: "opacity, transform",
              }}
            >
              {w}
            </span>
          </span>
        );
      })}
    </Tag>
  );
}

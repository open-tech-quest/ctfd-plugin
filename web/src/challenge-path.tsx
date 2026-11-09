import {
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import type { Step } from "./types";
import { Arrow, Check } from "./ui";
import { pageTarget, railBounds, revealTarget } from "./challenge-path-layout";

export function ChallengePath({
  steps,
  selectedId,
  onSelect,
  style,
}: {
  steps: Step[];
  selectedId: number | undefined;
  onSelect: (id: number) => void;
  style: CSSProperties;
}) {
  const id = useId();
  const rail = useRef<HTMLOListElement>(null);
  const [bounds, setBounds] = useState({
    overflowing: false,
    previous: false,
    next: false,
  });
  const stepIds = steps.map((step) => step.id).join(",");

  useLayoutEffect(() => {
    const element = rail.current!;
    const measure = () => {
      const next = railBounds(element);
      setBounds((previous) =>
        previous.overflowing === next.overflowing &&
        previous.previous === next.previous &&
        previous.next === next.next
          ? previous
          : next,
      );
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    for (const item of element.children) observer.observe(item);
    element.addEventListener("scroll", measure, { passive: true });
    measure();
    return () => {
      observer.disconnect();
      element.removeEventListener("scroll", measure);
    };
  }, [stepIds]);

  function reveal(item: HTMLElement) {
    const element = rail.current!;
    element.scrollTo({
      left: revealTarget(element, item.offsetLeft, item.offsetWidth),
      behavior: "instant",
    });
  }

  useLayoutEffect(() => {
    const item = rail.current?.querySelector<HTMLElement>(
      `[data-step-id="${selectedId}"]`,
    );
    if (item) reveal(item);
    // Polling replaces step objects; only selection/topology changes reveal.
  }, [selectedId, stepIds]);

  function scroll(direction: -1 | 1) {
    const element = rail.current!;
    element.scrollTo({
      left: pageTarget(
        element,
        direction,
        Array.from(
          element.children,
          (item) => (item as HTMLElement).offsetLeft,
        ),
      ),
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
    });
  }

  return (
    <section
      className="challenge-path"
      style={style}
      aria-labelledby={`${id}-heading`}
    >
      <div className="challenge-path-heading">
        <div>
          <h2 id={`${id}-heading`}>Challenge path</h2>
          <span>
            {steps.length} {steps.length === 1 ? "challenge" : "challenges"}
          </span>
        </div>
        {bounds.overflowing && (
          <div className="challenge-path-controls">
            <button
              type="button"
              aria-label="Previous challenges"
              aria-controls={`${id}-rail`}
              disabled={!bounds.previous}
              onClick={() => scroll(-1)}
            >
              <Arrow />
            </button>
            <button
              type="button"
              aria-label="Next challenges"
              aria-controls={`${id}-rail`}
              disabled={!bounds.next}
              onClick={() => scroll(1)}
            >
              <Arrow />
            </button>
          </div>
        )}
      </div>
      <ol
        ref={rail}
        id={`${id}-rail`}
        className="step-path"
        aria-labelledby={`${id}-heading`}
        tabIndex={bounds.overflowing ? 0 : undefined}
        onFocusCapture={(event) => {
          const item = (event.target as HTMLElement).closest<HTMLElement>("li");
          if (item) reveal(item);
        }}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget) return;
          if (event.key === "ArrowLeft" || event.key === "PageUp") {
            event.preventDefault();
            scroll(-1);
          } else if (event.key === "ArrowRight" || event.key === "PageDown") {
            event.preventDefault();
            scroll(1);
          } else if (event.key === "Home" || event.key === "End") {
            event.preventDefault();
            event.currentTarget.scrollTo({
              left: event.key === "Home" ? 0 : event.currentTarget.scrollWidth,
              behavior: "instant",
            });
          }
        }}
      >
        {steps.map((step, index) => (
          <li
            key={step.id}
            data-step-id={step.id}
            className={`step-${step.state} ${selectedId === step.id ? "selected" : ""}`}
          >
            <button
              type="button"
              disabled={step.state === "locked"}
              onClick={() => onSelect(step.id)}
              aria-current={selectedId === step.id ? "step" : undefined}
            >
              <span className="step-number">
                {step.state === "solved" ? <Check /> : index + 1}
              </span>
              <span className="step-label">
                <small>
                  {step.state === "solved"
                    ? "Solved"
                    : step.state === "locked"
                      ? "Locked"
                      : "Available"}
                </small>
                <strong>{step.title}</strong>
                <span className="step-points">
                  {step.value.toLocaleString()} pts
                </span>
              </span>
            </button>
          </li>
        ))}
      </ol>
    </section>
  );
}

import * as React from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";

import { cn } from "./utils";

/** Match @radix-ui/react-slider thumb placement (step index, not raw value range). */
function sliderValuePercent(value: number, min: number, max: number): number {
  if (max <= min) return 0;
  const clamped = Math.max(min, Math.min(max, value));
  const maxSteps = max - min;
  return ((clamped - min) / maxSteps) * 100;
}

function Slider({
  className,
  defaultValue,
  value,
  min = 0,
  max = 100,
  thumbLabel,
  centerTickIndex,
  fillToValue,
  fillColor,
  hideRange = false,
  ...props
}: React.ComponentProps<typeof SliderPrimitive.Root> & {
  thumbLabel?: React.ReactNode;
  /** Draw a notch on the track at this slider index (e.g. 1 = centre when min=0 max=2). */
  centerTickIndex?: number;
  /** Fill the track from the start through this value (same % mapping as the thumb). */
  fillToValue?: number;
  fillColor?: string;
  /** Hide the default range fill (use with fillToValue + thumb for dual indicators). */
  hideRange?: boolean;
}) {
  const _values = React.useMemo(
    () =>
      Array.isArray(value)
        ? value
        : Array.isArray(defaultValue)
          ? defaultValue
          : [min, max],
    [value, defaultValue, min, max],
  );

  return (
    <SliderPrimitive.Root
      data-slot="slider"
      defaultValue={defaultValue}
      value={value}
      min={min}
      max={max}
      className={cn(
        "relative flex w-full touch-none items-center select-none data-[disabled]:opacity-50 data-[orientation=vertical]:h-full data-[orientation=vertical]:min-h-44 data-[orientation=vertical]:w-auto data-[orientation=vertical]:flex-col",
        "[&_[data-slot=slider-track]]:z-0",
        "[&>span:has([data-slot=slider-thumb])]:z-10",
        className,
      )}
      {...props}
    >
      <SliderPrimitive.Track
        data-slot="slider-track"
        className={cn(
          "bg-muted relative grow overflow-hidden rounded-full data-[orientation=horizontal]:h-4 data-[orientation=horizontal]:w-full data-[orientation=vertical]:h-full data-[orientation=vertical]:w-1.5",
        )}
      >
        {fillToValue != null && (
          <div
            className="pointer-events-none absolute top-0 left-0 z-[1] h-full rounded-full transition-[width] duration-200"
            style={{
              width: `${sliderValuePercent(fillToValue, min, max)}%`,
              background: fillColor ?? "rgba(100, 130, 165, 0.55)",
            }}
          />
        )}
        {centerTickIndex != null && (
          <div
            className="pointer-events-none absolute top-0 bottom-0 z-[2] w-px -translate-x-1/2"
            style={{
              left: `${sliderValuePercent(centerTickIndex, min, max)}%`,
              background: "var(--text-muted)",
              opacity: 0.45,
            }}
          />
        )}
        <SliderPrimitive.Range
          data-slot="slider-range"
          className={cn(
            "bg-primary absolute z-[2] data-[orientation=horizontal]:h-full data-[orientation=vertical]:w-full",
            hideRange && "opacity-0",
          )}
        />
      </SliderPrimitive.Track>
      {Array.from({ length: _values.length }, (_, index) => (
        <SliderPrimitive.Thumb
          data-slot="slider-thumb"
          key={index}
          className={cn(
            "border-primary bg-background ring-ring/50 relative z-10 block size-4 shrink-0 rounded-full border shadow-sm transition-[color,box-shadow] hover:ring-4 focus-visible:ring-4 focus-visible:outline-hidden disabled:pointer-events-none disabled:opacity-50",
          )}
        >
          {thumbLabel != null && (
            <span
              className="pointer-events-none absolute left-1/2 bottom-[calc(100%+6px)] -translate-x-1/2 whitespace-nowrap uppercase tracking-wider"
              style={{
                fontSize: 8,
                fontWeight: 700,
                fontFamily: 'monospace',
                lineHeight: 1.2,
              }}
            >
              {thumbLabel}
            </span>
          )}
        </SliderPrimitive.Thumb>
      ))}
    </SliderPrimitive.Root>
  );
}

export { Slider };

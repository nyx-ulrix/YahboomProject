// react-grid-layout wrapper for dynamically rendered widgets.
import { useMemo, useState, useEffect, useRef } from 'react';
import GridLayout, { verticalCompactor, type LayoutItem } from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import {
  useLayoutStore,
  layoutBreakpointFromWidth,
  type LayoutBreakpoint,
  type LayoutStore,
} from '../store';
import { WIDGET_BY_ID } from './Widgets';
import { WidgetWrapper } from './WidgetWrapper';

const COLS = 12;
const GAP = 8; // margin between cells (px)

/** Compute row height so each grid unit is a perfect square. */
function squareRowHeight(containerWidth: number): number {
  return Math.floor((containerWidth - GAP * (COLS - 1)) / COLS);
}

export function DashboardGrid() {
  const lockedIds = useLayoutStore((s: LayoutStore) => s.lockedIds);
  const laptopLayout = useLayoutStore((s: LayoutStore) => s.laptopLayout);
  const ipadLayout = useLayoutStore((s: LayoutStore) => s.ipadLayout);
  const setLayout = useLayoutStore((s: LayoutStore) => s.setLayout);
  const setActiveBreakpoint = useLayoutStore((s: LayoutStore) => s.setActiveBreakpoint);
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1200);

  const breakpoint: LayoutBreakpoint = layoutBreakpointFromWidth(width);
  const layout = breakpoint === 'laptop' ? laptopLayout : ipadLayout;

  useEffect(() => {
    setActiveBreakpoint(breakpoint);
  }, [breakpoint, setActiveBreakpoint]);

  const effectiveLayout = useMemo(
    () => layout.map((l) => ({ ...l, static: lockedIds.includes(l.i) })),
    [layout, lockedIds]
  );

  // Layout is persisted. If a widget definition changes (e.g. was fixed-size,
  // now resizable), older saved layout items can still have maxW/maxH set and
  // block resizing. Normalize here to keep existing dashboards working.
  useEffect(() => {
    let changed = false;
    const next = layout.map((l) => {
      if (l.i !== 'lidar_scan_widget') return l;
      if (l.maxW != null || l.maxH != null) {
        changed = true;
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        const { maxW, maxH, ...rest } = l as LayoutItem & { maxW?: number; maxH?: number };
        return rest as LayoutItem;
      }
      return l;
    });
    if (changed) setLayout(next, breakpoint);
  }, [layout, setLayout, breakpoint]);

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setWidth(e.contentRect.width);
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  const rowHeight = squareRowHeight(width);

  const items = useMemo(
    () => layout.filter((l) => WIDGET_BY_ID[l.i]).map((l) => ({ l, def: WIDGET_BY_ID[l.i] })),
    [layout]
  );

  return (
    <div ref={containerRef} className="w-full">
      <GridLayout
        className="layout"
        layout={effectiveLayout}
        width={width}
        gridConfig={{ cols: COLS, rowHeight, margin: [GAP, GAP], containerPadding: [0, 0] }}
        dragConfig={{ handle: '.widget-drag-handle', cancel: '.widget-no-drag', enabled: true, bounded: false, threshold: 3 }}
        compactor={verticalCompactor}
        onLayoutChange={(next) => setLayout([...next], breakpoint)}
      >
        {items.map(({ l, def }) => {
          const Comp = def.component;
          return (
            <div key={l.i} data-grid={l}>
              <WidgetWrapper def={def}>
                <Comp />
              </WidgetWrapper>
            </div>
          );
        })}
      </GridLayout>
    </div>
  );
}